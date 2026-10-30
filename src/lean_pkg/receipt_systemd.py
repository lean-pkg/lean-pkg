# lean-pkg receipt systemd -- a lower-tier receipt from a finished systemd --user service unit
"""`lean-pkg receipt systemd --unit U --log PATH --commit SHA [--properties-file F | --run [caps] -- CMD ...]
[--stdout PATH] [--stderr PATH] [--checker TEXT] [--repo DIR] [--redact REGEX ...] [--out PATH]
[--append-readme ...] [--json]` (alias `receipt local`): the local-executor counterpart of `receipt slurm`. A
verify script run in a capped `systemd-run --user` service unit becomes a lower-tier receipt in exactly the shape
`gate --receipt` validates, through the same seal, field, redaction and public-safety pipeline
(`receipt.produce`). It never reads or writes a registry.

Where the unit's terminal state comes from (one of three, like sacct / --sacct-file):

- `--run -- CMD ...`: lean-pkg starts CMD itself as the transient unit U with
  `systemd-run --user --unit=U -p RemainAfterExit=yes -p WorkingDirectory=<repo> -p OOMPolicy=stop
  -p MemoryMax=.. -p MemorySwapMax=.. -p CPUQuota=.. -p RuntimeMaxSec=..` (bounded by default), the unit's
  stdout/stderr going to --stdout/--stderr (truncate:). RemainAfterExit keeps the finished unit loaded, so
  nothing is collected before lean-pkg has read it: it polls `systemctl --user show U` until the unit is
  terminal, captures UNIT_PROPS (plus `Hostname=` of this system) to --properties-out (default
  `<log>.unit.properties`, written even when the receipt is later refused), and only then stops and
  resets the unit. `--wait --collect` is not used: with --collect the unit and its properties vanish the moment
  it finishes. The checkout must be at --commit with no tracked change and no untracked file under the
  package directory before the run, and still be after it (untracked: what `git ls-files --others
  --exclude-standard` lists, less .lean-receipts/, .lake/ and the run's own output files; files git ignores
  are not checked, so they are not bound). Lake's local artifact cache is isolated for the unit: unless
  --setenv names them, it gets LAKE_CACHE_DIR=<a fresh empty temporary directory, removed afterwards> and
  LAKE_ARTIFACT_CACHE=false, so no module is restored (`Reused`) without its sorry warning (lake_hygiene).
  An explicit --setenv LAKE_CACHE_DIR=DIR is used as given (it should be empty); LAKE_CACHE_DIR= (empty, which
  does not disable the cache) and a LAKE_ARTIFACT_CACHE other than false/0 are usage errors. Interrupted while
  waiting (SIGINT, SIGTERM, SIGHUP), the unit is left running and its temporary cache is kept while the unit may
  still run; the recovery command (the same without --run) is printed, exit 128 + the signal (report_interrupt). A log showing a
  root-package module Reused, Replayed or Unpacked is refused, so run from a clean build (`lake clean`).
  The running lean-pkg itself is fingerprinted before the start (producer.py: its package files, dist-info,
  interpreter and entry point), checked about every PRODUCER_CHECK_SECONDS while waiting (a WARN, once) and
  again at collection: a change refuses, naming the files; an unchanged one is sealed as the additive receipt
  key `producer` (gate ignores it) and a `- Producer:` README line.
  `--require-tools A,B,...` (repeatable): before the unit is created, each project-declared executable must
  resolve like bash `type -P` in the unit's PATH (this process's, or the last --setenv PATH=); the resolved
  path, its real path and SHA-256 are printed to stderr and returned as --json `run_tools`; a missing one
  refuses and nothing is run. It checks only the names given, not their libraries, modules or what they call.
- `--properties-file F`: the output of `systemctl --user show U -p <UNIT_PROPS>` saved before the unit was
  collected, plus a `Hostname=` line naming the host it ran on (the host is part of job_id).
- neither: `systemctl --user show U` now, on this host (the unit must still be loaded, i.e. not --collect).

The exported property set is exactly UNIT_PROPS plus Hostname: Id, LoadState, ActiveState, SubState, Result,
InvocationID, ExecMainCode, ExecMainStatus, ExecMainStartTimestamp, ExecMainExitTimestamp,
ExecMainStartTimestampMonotonic, ExecMainExitTimestampMonotonic, NRestarts, MemoryPeak, CPUUsageNSec, MemoryMax,
MemorySwapMax, CPUQuotaPerSecUSec, RuntimeMaxUSec, OOMPolicy, RemainAfterExit. Only these keys are read and
copied into the evidence (any other line is ignored); REQUIRED must be present, the others are recorded when
present and never required. MainPID is not exported (0 once the main process has exited).

Unit checks (every failure listed; any one refuses, exit 1, nothing written): LoadState=loaded and Id = U; a
32-hex InvocationID; terminal (ActiveState failed or inactive, or active with SubState exited); Result=success
(oom-kill, timeout, signal, exit-code ... are named); ExecMainCode exited (1) with ExecMainStatus 0 (killed or
dumped: the signal is named); NRestarts 0. Then everything `receipt slurm` checks: the stdout seal
(verify-log-sha256, pre = post manifest = committed lake-manifest.json; or the in-log fallback seal), an
optional stdout `invocation-id` line equal to InvocationID, empty --stderr, toolchain / mathlib / sorry /
axioms / checker / commit: lines, redaction and the public-safety scan.

Output: job_id `systemd-user:<U>:<InvocationID>@<host label>`, the host label being `h-` and the first 12 hex
digits of the SHA-256 of the lower-cased host name (`host_label`; the name itself is never written, and the
public-safety scan blocks this host's name); beside the receipt `<c12>-<slug>.json` and redacted log, the
normalised properties `<c12>-<slug>-unit.properties` (Hostname as its label; its SHA-256 in the additive receipt
key `unit_properties_sha256`, gate ignores it) and a README entry with Result, exit, runtime, CPU, memory peak and
the caps. Built-in redactions: the checkout's absolute path -> `<repo>`, $HOME -> `<HOME>`, `/run/user/N/` ->
`/run/user/<UID>/`, then every --redact.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

from . import gate as gate_mod
from . import core
from . import producer as pr
from . import receipt as rc

UNIT_PROPS = ("Id", "LoadState", "ActiveState", "SubState", "Result", "InvocationID", "ExecMainCode",
              "ExecMainStatus", "ExecMainStartTimestamp", "ExecMainExitTimestamp",
              "ExecMainStartTimestampMonotonic", "ExecMainExitTimestampMonotonic", "NRestarts", "MemoryPeak",
              "CPUUsageNSec", "MemoryMax", "MemorySwapMax", "CPUQuotaPerSecUSec", "RuntimeMaxUSec", "OOMPolicy",
              "RemainAfterExit")
# The exported set is exactly UNIT_PROPS (plus Hostname). REQUIRED must be present; the rest are recorded when
# present and never required, so a capture made with an older, shorter list (RemainAfterExit was added in
# 0.13.0) stays valid. MainPID is deliberately not exported: at terminal capture the main process is gone and
# systemd reports MainPID=0, and a PID is host-local and recycled; InvocationID identifies the run.
REQUIRED = ("Id", "LoadState", "ActiveState", "SubState", "Result", "InvocationID", "ExecMainCode",
            "ExecMainStatus", "ExecMainExitTimestampMonotonic")
HOST_KEY = "Hostname"
STATE_PROPS = "LoadState,ActiveState,SubState,ExecMainExitTimestampMonotonic"
INVOCATION_KEY = "invocation-id"
# siginfo si_code values systemd reports as ExecMainCode (CLD_EXITED, CLD_KILLED, CLD_DUMPED)
CODES = {"1": "exited", "2": "killed", "3": "dumped", "exited": "exited", "killed": "killed", "dumped": "dumped"}
DEFAULT_CAPS = {"memory_max": "8G", "memory_swap_max": "0", "cpu_quota": "400%", "runtime_max": "4h"}
POLL_SECONDS = 2.0
# --run re-fingerprints the running lean-pkg this often while it waits (producer.py); and always at collection
PRODUCER_CHECK_SECONDS = 60.0
SYSTEMCTL_TIMEOUT = 60
_CAP_RE = re.compile(r"[0-9][0-9.]*[A-Za-z%]*")
_UNIT_RE = re.compile(r"[A-Za-z0-9:_.@\\-]+")
_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_TOOL_RE = re.compile(r"[A-Za-z0-9_.+][A-Za-z0-9_.+-]*")
_OTHER_TYPE_RE = re.compile(r"\.(scope|socket|timer|target|mount|automount|path|slice|swap|device)$")


# --------------------------------------------------------------------------
# talking to the user manager

def user_env() -> dict:
    """os.environ, plus the user bus when the caller's session lacks it (over ssh `systemctl --user` otherwise
    fails with "Access denied" / "Failed to connect to bus")."""
    env = dict(os.environ)
    rt = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if "XDG_RUNTIME_DIR" not in env and os.path.isdir(rt):
        env["XDG_RUNTIME_DIR"] = rt
    if "DBUS_SESSION_BUS_ADDRESS" not in env and os.path.exists(os.path.join(rt, "bus")):
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={rt}/bus"
    return env


def systemctl_show(exe: str, unit: str, props: str) -> tuple[str | None, str | None]:
    """(`systemctl --user show UNIT -p PROPS` output, refusal)."""
    try:
        p = subprocess.run([exe, "--user", "show", unit, "-p", props], capture_output=True, text=True,
                           timeout=SYSTEMCTL_TIMEOUT, env=user_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"systemctl could not run: {e}"
    if p.returncode != 0:
        return None, (f"systemctl --user show exited {p.returncode}: {(p.stderr or p.stdout).strip() or 'no output'}"
                      " (over ssh the user bus needs XDG_RUNTIME_DIR=/run/user/$UID)")
    return p.stdout, None


def _quiet(argv: list[str]) -> None:
    try:
        subprocess.run(argv, capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT, env=user_env())
    except (OSError, subprocess.TimeoutExpired):
        pass


# --------------------------------------------------------------------------
# properties

def parse_properties(text: str) -> tuple[dict, list[str]]:
    """(KEY -> value for the recognised UNIT_PROPS and Hostname lines, refusals). Other keys are ignored and
    never copied into the evidence; a recognised key given twice refuses."""
    seen: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, eq, value = line.rstrip("\r").partition("=")
        if eq and key in UNIT_PROPS + (HOST_KEY,):
            seen.setdefault(key, []).append(value.strip())
    props, refusals = {}, []
    for key, values in seen.items():
        if len(values) > 1:
            refusals.append(f"unit properties: `{key}=` appears {len(values)} times; exactly one is allowed")
        else:
            props[key] = values[0]
    return props, refusals


def host_label(name: str) -> str:
    """The host as published (job_id, README, the properties evidence): `h-` and the first 12 hex digits of
    the SHA-256 of the lower-cased host name. Distinct hosts stay distinct and anyone holding the name can
    recompute the label, but the name itself (often a user or site identifier) is never written; the
    receipt's public-safety scan blocks this host's name."""
    return "h-" + hashlib.sha256(name.strip().lower().encode("utf-8")).hexdigest()[:12]


def normalised(props: dict) -> bytes:
    """The evidence file: the recognised properties in UNIT_PROPS order, then Hostname as its label."""
    keys = [k for k in UNIT_PROPS + (HOST_KEY,) if k in props]
    return "".join(f"{k}={host_label(props[k]) if k == HOST_KEY else props[k]}\n" for k in keys).encode("utf-8")


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def check_unit(props: dict, unit: str) -> tuple[dict | None, list[str], list[str]]:
    """(summary, refusals, checks): the unit is this one, terminal, and finished Result=success, exit 0."""
    refusals: list[str] = []
    ls = props.get("LoadState")
    if ls is not None and ls != "loaded":
        return None, [f"unit {unit}: not found (LoadState={ls}); a unit started with --collect is gone once it "
                      f"finishes: capture `systemctl --user show` before it is collected, or use --run"], []
    missing = [k for k in REQUIRED if k not in props]
    if missing:
        return None, [f"unit properties: no {', '.join(k + '=' for k in missing)} line(s) (capture them with "
                      f"`systemctl --user show {unit} -p {','.join(UNIT_PROPS)}`)"], []
    if props["Id"] != unit:
        refusals.append(f"unit properties are for {props['Id']!r}, not --unit {unit}")
    inv = props["InvocationID"].lower()
    if not re.fullmatch(r"[0-9a-f]{32}", inv):
        refusals.append(f"unit {unit}: InvocationID {props['InvocationID']!r} is not 32 hex characters")
    host = props.get(HOST_KEY, "")
    if not host:
        refusals.append(f"unit properties: no `{HOST_KEY}=` line; append `{HOST_KEY}=$(hostname)` on the host "
                        f"the unit ran on (the host is part of job_id)")
    elif not _HOST_RE.fullmatch(host):
        refusals.append(f"unit properties: `{HOST_KEY}=` {host!r} is not a host name")
    active, subst = props["ActiveState"], props["SubState"]
    terminal = active in ("failed", "inactive") or (active == "active" and subst == "exited")
    code = CODES.get(props["ExecMainCode"])
    status = _int(props["ExecMainStatus"])
    exited_at = _int(props["ExecMainExitTimestampMonotonic"]) or 0
    if not terminal:
        refusals.append(f"unit {unit} is not terminal (ActiveState={active}, SubState={subst}); a receipt needs "
                        f"the finished unit")
    elif not exited_at or code is None:
        refusals.append(f"unit {unit}: its main process never exited (ExecMainCode={props['ExecMainCode']}, "
                        f"ExecMainExitTimestampMonotonic={props['ExecMainExitTimestampMonotonic']})")
    result = props["Result"]
    if result != "success":
        why = {"oom-kill": f"the OOM killer stopped it (MemoryMax={props.get('MemoryMax', 'unknown')})",
               "timeout": f"the runtime cap stopped it (RuntimeMaxUSec={props.get('RuntimeMaxUSec', 'unknown')})",
               "signal": "it was killed by a signal", "core-dump": "it dumped core",
               "exit-code": "its main process exited non-zero"}.get(result, "not a clean finish")
        refusals.append(f"unit {unit}: Result={result}: {why}; a receipt needs Result=success")
    if code in ("killed", "dumped"):
        refusals.append(f"unit {unit}: main process {code} by signal {props['ExecMainStatus']} "
                        f"(ExecMainCode={props['ExecMainCode']}); a receipt needs a clean exit 0")
    elif code == "exited" and status != 0:
        refusals.append(f"unit {unit}: main process exited {props['ExecMainStatus']}; a receipt needs exit 0")
    restarts = _int(props.get("NRestarts", "0"))
    if restarts:
        refusals.append(f"unit {unit}: NRestarts={restarts}; a receipt binds one run of the unit")
    start = _int(props.get("ExecMainStartTimestampMonotonic"))
    summary = {"id": props["Id"], "invocation_id": inv, "host": None, "active_state": active,
               "sub_state": subst, "result": result, "exec_main_code": code or props["ExecMainCode"],
               "exec_main_status": props["ExecMainStatus"],
               "runtime_usec": exited_at - start if start and exited_at >= start else None,
               "cpu_usage_nsec": _int(props.get("CPUUsageNSec")), "memory_peak": _int(props.get("MemoryPeak")),
               "memory_max": props.get("MemoryMax"), "memory_swap_max": props.get("MemorySwapMax"),
               "cpu_quota_per_sec": props.get("CPUQuotaPerSecUSec"), "runtime_max": props.get("RuntimeMaxUSec"),
               "remain_after_exit": props.get("RemainAfterExit")}
    if host and _HOST_RE.fullmatch(host):
        summary["host"] = host_label(host)       # the name itself is never published
    checks = []
    if not refusals:
        checks.append(f"✓ unit: {unit} invocation {inv[:12]}… on {summary['host']}: Result=success, exited 0")
    checks.append(f"· resources: {resources(summary)}")
    return summary, refusals, checks


def _bytes(v) -> str:
    n = _int(v)
    if n is None:
        return "none" if str(v) == "infinity" else "unknown"
    for unit, size in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:05.2f}"


def _quota(v) -> str:
    """CPUQuotaPerSecUSec (`4s`, `500ms`, `1s 500ms`, `infinity`) as the CPUQuota percentage."""
    if v is None:
        return "unknown"
    if v == "infinity":
        return "none"
    parts = re.findall(r"(\d+(?:\.\d+)?)(us|ms|s)\b", v)
    if not parts or "".join(a + b for a, b in parts) != v.replace(" ", ""):
        return v
    total = sum(float(a) * {"us": 1e-6, "ms": 1e-3, "s": 1.0}[b] for a, b in parts)
    return f"{total * 100:g}%"


def resources(s: dict) -> str:
    rt = s["runtime_usec"] / 1e6 if s["runtime_usec"] is not None else None
    cpu = s["cpu_usage_nsec"] / 1e9 if s["cpu_usage_nsec"] is not None else None
    runtime_max = s["runtime_max"] or "unknown"
    return (f"runtime {_clock(rt)}, CPU {_clock(cpu)}, memory peak {_bytes(s['memory_peak'])}; caps MemoryMax "
            f"{_bytes(s['memory_max'])}, MemorySwapMax {_bytes(s['memory_swap_max'])}, CPUQuota "
            f"{_quota(s['cpu_quota_per_sec'])}, RuntimeMax {'none' if runtime_max == 'infinity' else runtime_max}")


def redactions(repo: pathlib.Path) -> list[tuple[str, str]]:
    """The checkout's absolute path (as given and resolved) -> <repo>, $HOME -> <HOME>, the runtime dir's uid:
    a local run prints these, and a committed evidence file must not carry them."""
    out, seen = [], set()
    pairs = [(os.path.abspath(repo), "<repo>"), (str(repo.resolve()), "<repo>")]
    home = os.environ.get("HOME")
    if home:
        pairs += [(os.path.abspath(home), "<HOME>"), (os.path.realpath(home), "<HOME>")]
    for path, token in sorted(pairs, key=lambda x: -len(x[0])):
        path = path.rstrip("/")
        if len(path) > 1 and path not in seen:
            seen.add(path)
            out.append((re.escape(path) + r"(?![A-Za-z0-9._-])", token))
    out.append((r"/run/user/\d+/", "/run/user/<UID>/"))
    return out


# --------------------------------------------------------------------------
# --run

# Untracked paths under the package directory that a local run may leave without changing the source: the
# evidence directory and Lake's build state. Ignored files (.gitignore) are not listed and not bound.
UNTRACKED_OK = (".lean-receipts/", ".lake/")


def tree_state(repo: pathlib.Path, exclude=()) -> tuple[str | None, str, list[str]]:
    """(HEAD, porcelain status of tracked files, untracked files) of the package directory `repo`. The
    pathspec `.` scopes both to it: a package incubated in a subdirectory of a manuscript repository is not
    made dirty by an edit outside it, as the evidence-only rule ignores those paths too. Untracked files are
    the ones git does not ignore (`git ls-files --others --exclude-standard`, package-relative), less
    UNTRACKED_OK and the package-relative paths in `exclude` (the run's own log, stdout, stderr and
    properties files)."""
    head = core.git_head(repo)
    p = core.git(repo, "status", "--porcelain", "--untracked-files=no", "--", ".")
    dirty = p.stdout if p.returncode == 0 else f"git status failed: {p.stderr.strip()}"
    u = core.git(repo, "ls-files", "-z", "--others", "--exclude-standard", "--", ".")
    if u.returncode != 0:
        return head, dirty or f"git ls-files failed: {u.stderr.strip()}", []
    skip = set(exclude)
    untracked = [x for x in u.stdout.split("\0") if x and x not in skip and not x.startswith(UNTRACKED_OK)]
    return head, dirty, untracked


def run_outputs(repo: pathlib.Path, files) -> list[str]:
    """The package-relative paths of the files a run writes that lie inside the package directory."""
    out = []
    root = repo.resolve()
    for f in files:
        try:
            out.append(pathlib.Path(os.path.abspath(f)).resolve().relative_to(root).as_posix())
        except (ValueError, OSError):
            pass
    return out


def source_problem(repo: pathlib.Path, commit: str | None, when: str, exclude=()) -> str | None:
    if commit is None:
        return None                        # produce() refuses the unknown commit
    head, dirty, untracked = tree_state(repo, exclude)
    if head != commit:
        return (f"source changed {when}: the checkout's HEAD is {(head or 'unknown')[:12]}, not --commit "
                f"{commit[:12]}; a local run verifies the working tree, so it must be the commit")
    if dirty.strip():
        n = len(dirty.strip().splitlines())
        return f"source changed {when}: {n} tracked file(s) differ from --commit {commit[:12]}"
    if untracked:
        shown = ", ".join(untracked[:5]) + (f" (+{len(untracked) - 5} more)" if len(untracked) > 5 else "")
        return (f"source changed {when}: {len(untracked)} untracked file(s) under the package directory, "
                f"which the build can read but --commit {commit[:12]} does not hold: {shown}; commit, remove "
                f"or .gitignore them (ignored files are not bound)")
    return None


class RunInterrupted(Exception):
    """`receipt systemd --run` stopped by a signal (SIGINT, SIGTERM, SIGHUP) while the unit was started or
    polled; cmd_receipt_systemd exits 128 + signum."""

    def __init__(self, signum: int):
        super().__init__(f"interrupted by {signal.Signals(signum).name}")
        self.signum = signum


INTERRUPT_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def _set_handlers(handler) -> dict:
    """Install `handler` for INTERRUPT_SIGNALS (main thread only); the previous handlers, for _restore."""
    if threading.current_thread() is not threading.main_thread():
        return {}
    return {s: signal.signal(s, handler) for s in INTERRUPT_SIGNALS}


def _restore(old: dict) -> None:
    for s, h in old.items():
        signal.signal(s, h)


def _raise_interrupt(signum, _frame):
    raise RunInterrupted(signum)


def unit_running(ctl: str, unit: str) -> bool | None:
    """True while the unit (and so its main process) may still run, False when it is gone (not loaded) or
    terminal (failed, inactive, active+exited), None when its state cannot be read."""
    text, why = systemctl_show(ctl, unit, "LoadState,ActiveState,SubState")
    if why:
        return None
    state, _ = parse_properties(text)
    load = state.get("LoadState")
    if load is None:
        return None
    if load != "loaded" or state.get("ActiveState") in ("failed", "inactive") or state.get("SubState") == "exited":
        return False
    return True


def recovery_command(args, unit: str, repo: pathlib.Path, paths: dict, commit: str | None) -> str:
    """The `receipt systemd --unit U ...` (no --run) that writes the receipt from the unit once it finished."""
    argv = ["lean-pkg", "receipt", "systemd", "--unit", unit, "--log", str(paths["--log"]),
            "--receipt-commit", commit or args.commit_sha, "--repo", str(repo)]
    for flag in ("--stdout", "--stderr"):
        if flag in paths:
            argv += [flag, str(paths[flag])]
    if args.checker:
        argv += ["--checker", args.checker]
    for rx in args.redact or []:
        argv += ["--redact", rx]
    return " ".join(shlex.quote(a) for a in argv)


def report_interrupt(args, signum: int, unit: str, running: bool | None, cache_dir: str | None, repo, paths,
                     commit) -> None:
    """What an interrupted --run leaves and how to finish it (stderr): the unit is not stopped; its temporary
    Lake cache is kept while the unit may still run; the recovery command, which skips the "source tree
    unchanged during the run" check."""
    name = signal.Signals(signum).name
    state = {True: "is still running", False: "is no longer running", None: "is in an unknown state"}[running]
    err = sys.stderr
    print(f"lean-pkg: receipt systemd: interrupted by {name} while waiting for {unit}; the unit {state} and was "
          f"not stopped; nothing was written to .lean-receipts", file=err)
    if cache_dir is not None:
        if running is False:
            print("lean-pkg: its temporary LAKE_CACHE_DIR was removed (the unit no longer runs)", file=err)
        else:
            print(f"lean-pkg: its temporary LAKE_CACHE_DIR is kept at {cache_dir} (the unit may still use it); "
                  f"remove it once the unit has finished: rm -rf {shlex.quote(cache_dir)}", file=err)
    print("lean-pkg: to write the receipt once the unit has finished (it stays loaded: RemainAfterExit), run "
          "without --run, adding any other flags of this run (--out, --append-readme, ...):", file=err)
    print(f"  {recovery_command(args, unit, repo, paths, commit)}", file=err)
    print("lean-pkg: WARN recovery via --unit skips the --run check that the source tree was unchanged during the "
          "run (HEAD at --commit, no tracked change, no untracked file); confirm `git status` is clean yourself. "
          f"Then: systemctl --user stop {unit}; systemctl --user reset-failed {unit}", file=err)


def tool_names(values) -> list[str]:
    """The --require-tools names (comma-separated, repeatable), in order, each once."""
    names: list[str] = []
    for value in values or []:
        for name in (w.strip() for w in value.split(",")):
            if not name:
                continue
            if not _TOOL_RE.fullmatch(name):
                raise core.UsageError(f"--require-tools {name!r}: a command name (as `type -P` takes it, no '/')")
            if name not in names:
                names.append(name)
    return names


def unit_path(setenv) -> str:
    """The PATH the unit gets: this process's, unless a --setenv PATH=... replaces it (the last one wins, as in
    systemd's Environment=)."""
    path = os.environ.get("PATH", "")
    for kv in setenv or []:
        if kv.startswith("PATH="):
            path = kv[len("PATH="):]
    return path


def _file_sha256(path: str) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def require_tools(names: list[str], path: str) -> tuple[list[dict], list[str]]:
    """(resolved, missing): each name resolved like bash `type -P` in the unit's PATH (an executable file;
    never a keyword, builtin or function: `time` needs a binary), with its real path and SHA-256."""
    from .host_routes import type_p
    resolved, missing = [], []
    for name in names:
        hit = type_p(name, path)
        if hit is None:
            missing.append(name)
            continue
        real = os.path.realpath(hit)
        resolved.append({"name": name, "path": hit, "realpath": real, "sha256": _file_sha256(real)})
    return resolved, missing


def run_unit(args, unit: str, repo: pathlib.Path, paths: dict, props_out: pathlib.Path,
             refusals: list[str], checks: list[str], producer: dict | None = None) -> str | None:
    """Start the command as the bounded transient unit, wait for it to finish, capture its properties before
    the unit is stopped and reset; returns the captured text (None when it never ran). `producer` (a dict)
    receives the fingerprint of the running lean-pkg taken before the start ("start", producer.py), its
    receipt key as of the start ("receipt") and, when it changed by collection, the changed entries
    ("changed"; also a refusal)."""
    run_exe, ctl = shutil.which("systemd-run"), shutil.which("systemctl")
    if run_exe is None or ctl is None:
        refusals.append("systemd-run / systemctl are not on PATH; on a host without systemd run the unit "
                        "elsewhere and pass its saved properties with --properties-file")
        return None
    commit = rc.resolve_commit(repo, args.commit_sha)
    outputs = run_outputs(repo, [*paths.values(), props_out])
    why = source_problem(repo, commit, "before the run", outputs)
    if why:
        refusals.append(why + "; nothing was run")
        return None
    producer = {} if producer is None else producer
    producer["start"] = pr.fingerprint()
    producer["receipt"] = pr.receipt_producer(producer["start"])   # commit and dirtiness as of the start
    argv = [run_exe, "--user", f"--unit={unit}", "--quiet", "-p", "RemainAfterExit=yes",
            "-p", f"WorkingDirectory={repo.resolve()}", "-p", "OOMPolicy=stop",
            "-p", f"MemoryMax={args.memory_max}", "-p", f"MemorySwapMax={args.memory_swap_max}",
            "-p", f"CPUQuota={args.cpu_quota}", "-p", f"RuntimeMaxSec={args.runtime_max}"]
    for flag, prop in (("--stdout", "StandardOutput"), ("--stderr", "StandardError")):
        if flag in paths:
            argv += ["-p", f"{prop}=truncate:{paths[flag].resolve()}"]
    argv += [f"--setenv=PATH={os.environ.get('PATH', '')}"]
    argv += [f"--setenv={kv}" for kv in args.setenv or []]
    given = {kv.split("=", 1)[0] for kv in args.setenv or []}
    cache_dir = None
    if "LAKE_CACHE_DIR" not in given:        # a fresh, empty Lake cache of the unit's own (lake_hygiene)
        cache_dir = tempfile.mkdtemp(prefix="lean-pkg-lake-cache.")
        argv += [f"--setenv=LAKE_CACHE_DIR={cache_dir}"]
    if "LAKE_ARTIFACT_CACHE" not in given:
        argv += ["--setenv=LAKE_ARTIFACT_CACHE=false"]
    checks.append("✓ lake cache: " + ("a fresh empty LAKE_CACHE_DIR" if cache_dir else "LAKE_CACHE_DIR as --setenv")
                  + ", LAKE_ARTIFACT_CACHE=false" + (" (--setenv)" if "LAKE_ARTIFACT_CACHE" in given else ""))
    argv += ["--", *args.command]
    # Interrupted while the unit may still run (Ctrl-C or a SIGTERM while polling), its temporary Lake cache is
    # never deleted under it: the unit's state decides, and the recovery command is printed (report_interrupt).
    keep_cache = False
    old = _set_handlers(_raise_interrupt)
    try:
        return _run_started(argv, unit, ctl, repo, commit, outputs, props_out, refusals, checks, producer)
    except (KeyboardInterrupt, RunInterrupted) as e:
        _set_handlers(signal.SIG_IGN)            # no second interruption while the state is read and reported
        signum = e.signum if isinstance(e, RunInterrupted) else signal.SIGINT
        running = unit_running(ctl, unit)
        keep_cache = running is not False
        if cache_dir is not None and not keep_cache:
            shutil.rmtree(cache_dir, ignore_errors=True)
        report_interrupt(args, signum, unit, running, cache_dir, repo, paths, commit)
        raise RunInterrupted(signum) from None
    finally:
        _restore(old)
        if cache_dir is not None and not keep_cache:
            shutil.rmtree(cache_dir, ignore_errors=True)


def _producer_changes(producer: dict) -> list[str]:
    return pr.changes(producer["start"], pr.fingerprint(pathlib.Path(producer["start"]["pkg"])))


def _run_started(argv, unit, ctl, repo, commit, outputs, props_out, refusals, checks, producer) -> str | None:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT, env=user_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        refusals.append(f"systemd-run could not run: {e}")
        return None
    if p.returncode != 0:
        refusals.append(f"systemd-run exited {p.returncode}: {(p.stderr or p.stdout).strip() or 'no output'} "
                        f"(a unit named {unit} may already be loaded: `systemctl --user status {unit}`)")
        return None
    print(f"lean-pkg: receipt systemd: started {unit}; waiting for it to finish", file=sys.stderr)
    next_check, warned = time.monotonic() + PRODUCER_CHECK_SECONDS, False
    while True:
        text, why = systemctl_show(ctl, unit, STATE_PROPS)
        if why:
            refusals.append(why)
            return None
        state, _ = parse_properties(text)
        if (state.get("LoadState") != "loaded" or state.get("ActiveState") in ("failed", "inactive")
                or state.get("SubState") == "exited"):
            break
        if not warned and time.monotonic() >= next_check:
            # Early, not only at the end: the receipt of this run will be refused, say so while it runs.
            names = _producer_changes(producer)
            next_check = time.monotonic() + PRODUCER_CHECK_SECONDS
            if names:
                warned = True
                print(f"lean-pkg: receipt systemd: WARN {pr.changed_message(names, 'while ' + unit + ' runs')}; "
                      f"this run's receipt will be refused at collection (the unit keeps running; stop it with "
                      f"systemctl --user stop {unit})", file=sys.stderr)
        time.sleep(POLL_SECONDS)
    text, why = systemctl_show(ctl, unit, ",".join(UNIT_PROPS))
    if why is None:
        text = text.rstrip("\n") + f"\n{HOST_KEY}={socket.gethostname()}\n"
        props_out.write_text(text, encoding="utf-8")
        checks.append(f"✓ ran {unit}; properties captured before cleanup to {rc._shown(props_out, repo)}")
    else:
        refusals.append(why)
        text = None
    _quiet([ctl, "--user", "stop", unit])            # RemainAfterExit kept it loaded until now
    _quiet([ctl, "--user", "reset-failed", unit])
    why = source_problem(repo, commit, "during the run", outputs)
    if why:
        refusals.append(why)
    names = _producer_changes(producer)
    if names:
        producer["changed"] = names
        refusals.append(pr.changed_message(names, "during the run"))
    else:
        start = producer["start"]
        what = "; ".join([f"{len(start['sources'])} source files", *start["environment"]])
        checks.append(f"✓ producer: lean-pkg {start['version']} unchanged from run start to collection ({what})")
    return text


# --------------------------------------------------------------------------
# the command

def unit_name(value: str) -> str:
    unit = (value or "").strip()
    if not unit or unit.startswith("-") or not _UNIT_RE.fullmatch(unit):
        raise core.UsageError(f"--unit must name a systemd service unit, got {value!r}")
    if _OTHER_TYPE_RE.search(unit):
        raise core.UsageError(f"--unit {unit}: only service units carry a main process exit status")
    return unit if unit.endswith(".service") else unit + ".service"


def _refuse_early(args, job_id: str | None, refusals: list[str], checks: list[str], json_extra: dict) -> int:
    """Refuse before the shared pipeline can run (the unit never ran, or left no log)."""
    if args.json_out:
        payload = {"ok": False, "receipt": None, "log": None, "report_sha256": None, "raw_log_sha256": None,
                   "sealed": [],
                   "job_id": job_id, "seal": None, "log_commit_bound": None}
        payload.update(json_extra)
        payload.update({"redactions": 0, "lines_redacted": 0, "checks": checks, "readme_block": None,
                        "readme_appended": False, "next": None, "refusals": refusals})
        core.print_json(payload)
    verdict = f"receipt systemd: refused, {len(refusals)} problem(s); nothing written"
    print(f"lean-pkg: {verdict}", file=sys.stderr)
    for r in refusals:
        print(f"  ✗ {r}", file=sys.stderr)
    print(f"lean-pkg: {verdict}", file=sys.stderr)
    return 1


def cmd_receipt_systemd(args) -> int:
    unit = unit_name(args.unit)
    repo = rc.repo_dir(args)
    run = bool(args.run)
    if run and not args.command:
        raise core.UsageError("--run needs the command after `--` (e.g. --run -- bash verify.sh)")
    if args.command and not run:
        raise core.UsageError(f"a command ({args.command[0]!r} ...) is only run with --run")
    if run and args.properties_file:
        raise core.UsageError("--run and --properties-file are exclusive: --run captures the properties itself")
    if not run and args.properties_out:
        raise core.UsageError("--properties-out is only for --run")
    if run:
        for flag in ("memory_max", "memory_swap_max", "cpu_quota", "runtime_max"):
            v = getattr(args, flag)
            if not _CAP_RE.fullmatch(v or ""):
                raise core.UsageError(f"--{flag.replace('_', '-')} {v!r}: a finite systemd value (e.g. "
                                      f"{DEFAULT_CAPS[flag]}) is required; the unit is bounded")
        for kv in args.setenv or []:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", kv):
                raise core.UsageError(f"--setenv {kv!r}: expected NAME=VALUE")
            name, value = kv.split("=", 1)
            if name == "LAKE_CACHE_DIR" and not value.strip():
                raise core.UsageError("--setenv LAKE_CACHE_DIR= (empty) does not disable Lake's artifact cache "
                                      "(Lake then uses .lake/cache); leave it out and --run gives the unit a "
                                      "fresh empty one")
            if name == "LAKE_ARTIFACT_CACHE" and value.strip().lower() not in ("false", "0"):
                raise core.UsageError(f"--setenv {kv!r}: a receipt run needs LAKE_ARTIFACT_CACHE=false (a "
                                      f"restored module's sorry warning need not be printed)")
    elif args.setenv:
        raise core.UsageError("--setenv is only for --run")
    tools = tool_names(args.require_tools)
    if tools and not run:
        raise core.UsageError("--require-tools is only for --run (it is checked before the unit is created)")
    if getattr(args, "contracts_preflight", False) and not run:
        raise core.UsageError("--contracts-preflight is only for --run (it compares before the unit is created)")
    paths = {}
    for flag, value in (("--log", args.log), ("--stdout", args.stdout), ("--stderr", args.stderr)):
        if value is None:
            continue
        p = pathlib.Path(value).expanduser()
        if run:
            if p.exists() or p.is_symlink():
                raise core.UsageError(f"{flag} {value}: already exists; --run writes it fresh (remove it first)")
            if not p.parent.is_dir():
                raise core.UsageError(f"{flag} {value}: no such directory")
        elif not p.is_file():
            raise core.UsageError(f"{flag} {value}: no such file")
        paths[flag] = p
    props_out = None
    if run:
        props_out = pathlib.Path(args.properties_out or f"{paths['--log']}.unit.properties").expanduser()
        if props_out.exists() or props_out.is_symlink():
            raise core.UsageError(f"--properties-out {props_out.name}: already exists (remove it first)")
    if args.properties_file is not None and not pathlib.Path(args.properties_file).expanduser().is_file():
        raise core.UsageError(f"--properties-file {args.properties_file}: no such file")
    extra = rc.common_usage(args)

    refusals: list[str] = []
    checks: list[str] = []
    json_extra = {"unit": None, "properties": None, "ran": run, "producer": None}
    producer: dict = {}
    missing_tools: list[str] = []
    if run and tools:
        # Before the unit exists: every project-declared tool must resolve in the unit's own PATH.
        resolved, missing_tools = require_tools(tools, unit_path(args.setenv))
        json_extra["run_tools"] = {"required": tools, "resolved": resolved, "missing": missing_tools}
        for t in resolved:
            print(f"lean-pkg: receipt systemd: run tool {t['name']}: {t['path']}"
                  + (f" -> {t['realpath']}" if t["realpath"] != t["path"] else "")
                  + f" sha256 {t['sha256'] or 'unreadable'}", file=sys.stderr)
        if missing_tools:
            refusals.append(f"--require-tools: not found in the unit's PATH (type -P): {', '.join(missing_tools)}; "
                            f"nothing was run")
        else:
            checks.append(f"✓ run tools: {len(resolved)} resolved in the unit's PATH (type -P) before the unit "
                          f"was created: {', '.join(tools)}")
    contracts_refused = False
    if run and getattr(args, "contracts_preflight", False):
        # Before the unit exists: every comparator configuration must match (LO-20261030-04)
        from . import comparator_contracts as cc
        pre = cc.preflight(repo)
        json_extra["contracts_preflight"] = {"ok": pre["ok"], "report": pre["report"]}
        refusals += pre["refusals"]
        checks += pre["checks"]
        contracts_refused = not pre["ok"]
    if run and (missing_tools or contracts_refused):
        text = None
    elif run:
        try:
            text = run_unit(args, unit, repo, paths, props_out, refusals, checks, producer)
        except RunInterrupted as e:
            return 128 + e.signum
        if text is not None:
            json_extra["properties"] = str(props_out)
        if "receipt" in producer:
            json_extra["producer"] = {**producer["receipt"],
                                      "changed": producer.get("changed", [])}
    elif args.properties_file is not None:
        text = pathlib.Path(args.properties_file).expanduser().read_text(encoding="utf-8", errors="replace")
        json_extra["properties"] = str(args.properties_file)
    else:
        ctl = shutil.which("systemctl")
        if ctl is None:
            text = None
            refusals.append("systemctl is not on PATH; save `systemctl --user show U -p "
                            f"{','.join(UNIT_PROPS)}` plus a `{HOST_KEY}=` line where the unit ran and pass "
                            "--properties-file")
        else:
            text, why = systemctl_show(ctl, unit, ",".join(UNIT_PROPS))
            if why:
                refusals.append(why)
            else:
                text = text.rstrip("\n") + f"\n{HOST_KEY}={socket.gethostname()}\n"

    summary = None
    props: dict = {}
    if text is not None:
        props, problems = parse_properties(text)
        refusals += problems
        summary, problems, unit_checks = check_unit(props, unit)
        refusals += problems
        checks += unit_checks
    json_extra["unit"] = summary
    s = summary or {}
    host = s.get("host") or "unknown-host"
    inv = s.get("invocation_id") or "unknown"
    job_id = f"systemd-user:{unit}:{inv}@{host}"
    gate_mod.receipt_job_slug(job_id)
    if summary is None or not paths["--log"].is_file():
        if "--log" in paths and not paths["--log"].is_file():
            refusals.append(f"--log {paths['--log'].name}: the unit wrote no verify log there")
        return _refuse_early(args, job_id if summary else None, refusals, checks, json_extra)
    for flag in ("--stdout", "--stderr"):
        if flag in paths and not paths[flag].is_file():
            refusals.append(f"{flag} {paths[flag].name}: the unit's stream was not written")
            return _refuse_early(args, job_id, refusals, checks, json_extra)

    sealed_producer = None
    if json_extra["producer"] is not None and not json_extra["producer"]["changed"]:
        sealed_producer = {k: v for k, v in json_extra["producer"].items() if k != "changed"}

    def lines(receipt_name: str, tar: str) -> list[str]:
        out = [f"- Receipt: `{receipt_name}`. systemd --user unit `{unit}`, invocation `{inv}` on `{host}`:",
               f"  Result success, exit `0`; {resources(s)}{tar}."]
        if sealed_producer:
            p = sealed_producer
            build = f"editable {p['commit'][:12]}{'+dirty' if p['dirty'] else ''}" if p["commit"] else p["install"]
            code = (f", own bytecode ({p['bytecode_tag']}, {p['bytecode_files']} file(s)) SHA-256 "
                    f"`{p['bytecode_sha256']}`" if p.get("bytecode_sha256") else "")
            other = (f", {p['other_bytecode_files']} other bytecode file(s)"
                     if p.get("other_bytecode_files") else "")
            out.append(f"- Producer: lean-pkg {p['lean_pkg']} ({build}), sources SHA-256 `{p['sources_sha256']}`"
                       f"{code}{other}; sources unchanged from run start to collection.")
        return out

    def log_job_id(value: str) -> str | None:
        if value not in (job_id, inv):
            return f"verify log: `job-id:` {value!r} is neither the job_id {job_id!r} nor the InvocationID"
        return None

    def stdout_check(out_text: str, refs: list[str], chks: list[str]) -> None:
        vals = rc.stdout_values(out_text, INVOCATION_KEY)
        if len(vals) > 1:
            refs.append(f"job stdout: {len(vals)} `{INVOCATION_KEY}` lines; at most one is allowed")
        elif vals and vals[0].lower() != inv:
            refs.append(f"job stdout: {INVOCATION_KEY} {vals[0][:12]}… is not the unit's InvocationID "
                        f"{inv[:12]}…; the stdout is from another run")
        elif vals:
            chks.append(f"✓ stdout: {INVOCATION_KEY} = the unit's InvocationID")

    ev = rc.Evidence(verb="receipt systemd", job_id=job_id, refusals=refusals, checks=checks,
                     executor_lines=lines, default_heading=f"lower-tier receipt, systemd unit {unit}",
                     check_log_job_id=log_job_id, json_extra=json_extra, redactions=redactions(repo),
                     redaction_note="local paths redacted", stdout_check=stdout_check,
                     evidence_files=[("unit.properties", normalised(props), "unit_properties_sha256",
                                      "- Unit properties: `{name}`, SHA-256 `{sha}` (captured before the unit "
                                      "was collected).")],
                     receipt_extra={"producer": sealed_producer} if sealed_producer else None)
    return rc.produce(args, ev, repo, paths, extra)


def add_parser(rsub) -> None:
    s = rsub.add_parser("systemd",   # `receipt local` is an alias (command_tree.COMMAND_ALIASES)
                        help="a lower-tier receipt from a finished systemd --user service unit (run bounded with "
                             "--run, or its saved properties), its verify log and its stdout seal")
    s.add_argument("--unit", required=True, metavar="U",
                   help="the service unit (`.service` added when absent); job_id becomes "
                        "systemd-user:<U>:<InvocationID>@<host label> (h- + 12 hex of the host name's SHA-256)")
    s.add_argument("--log", required=True, metavar="PATH",
                   help="the raw verify log the unit writes; when it has no `checker:` line, --checker TEXT is "
                        "required")
    s.add_argument("--receipt-commit", dest="commit_sha", required=True, metavar="SHA",
                   help="the verified commit; must exist in --repo (with --run: the checkout's clean HEAD)")
    s.add_argument("--stdout", default=None, metavar="PATH",
                   help=f"the unit's stdout file: {rc.SHA_KEY} = sha256(raw log), {rc.PRE_KEY} = {rc.POST_KEY} = "
                        f"sha256(lake-manifest.json at the commit), optional {INVOCATION_KEY} = InvocationID; "
                        "without it the log must carry its own seal. With --run the unit writes it")
    s.add_argument("--stderr", default=None, metavar="PATH",
                   help="the unit's stderr file; must be empty. With --run the unit writes it")
    src = s.add_argument_group("where the unit's terminal state comes from (default: `systemctl --user show U` "
                               "now, on this host)")
    src.add_argument("--properties-file", default=None, metavar="F",
                     help=f"saved `systemctl --user show U -p {','.join(UNIT_PROPS)}` output, captured before the "
                          f"unit was collected, plus a `{HOST_KEY}=` line for the host it ran on")
    src.add_argument("--run", action="store_true",
                     help="start `-- CMD ...` as the bounded unit U (systemd-run --user, RemainAfterExit, "
                          "WorkingDirectory=--repo), wait for it, capture its properties before stopping it; "
                          "--log, --stdout, --stderr must not exist yet. The running lean-pkg's own files must "
                          "not change during the run (sealed as the receipt key `producer`)")
    src.add_argument("--memory-max", default=DEFAULT_CAPS["memory_max"], metavar="BYTES",
                     help=f"--run: MemoryMax (default {DEFAULT_CAPS['memory_max']})")
    src.add_argument("--memory-swap-max", default=DEFAULT_CAPS["memory_swap_max"], metavar="BYTES",
                     help=f"--run: MemorySwapMax (default {DEFAULT_CAPS['memory_swap_max']}, so the memory cap "
                          "is an OOM kill, not swapping)")
    src.add_argument("--cpu-quota", default=DEFAULT_CAPS["cpu_quota"], metavar="PCT",
                     help=f"--run: CPUQuota (default {DEFAULT_CAPS['cpu_quota'].replace('%', '%%')})")
    src.add_argument("--runtime-max", default=DEFAULT_CAPS["runtime_max"], metavar="SPAN",
                     help=f"--run: RuntimeMaxSec (default {DEFAULT_CAPS['runtime_max']})")
    src.add_argument("--setenv", action="append", default=[], metavar="NAME=VALUE",
                     help="--run: an extra environment variable for the unit (repeatable; PATH is passed; "
                          "LAKE_CACHE_DIR=<fresh empty dir> and LAKE_ARTIFACT_CACHE=false unless named here)")
    src.add_argument("--require-tools", action="append", default=[], metavar="A,B,...",
                     help="--run: executables the command needs (comma-separated, repeatable), each resolved "
                          "with `type -P` in the unit's PATH before the unit is created; a missing one refuses "
                          "and nothing is run. The resolved paths and SHA-256 are printed (stderr) and in --json "
                          "run_tools")
    src.add_argument("--contracts-preflight", action="store_true",
                     help="--run: before the unit is created, run the contracts-only diagnostic (`comparator check "
                          "--contracts-only`) on every comparator configuration of the checkout; a configuration "
                          "that differs, one whose modules are not built, or a diagnostic that cannot run refuses, "
                          "and nothing is run or written (docs/comparator.md, Before receipt work)")
    src.add_argument("--properties-out", default=None, metavar="PATH",
                     help="--run: where the captured properties go (default <log>.unit.properties)")
    s.add_argument("--redact", action="append", default=[], metavar="REGEX",
                   help=f"also replace matches with {rc.REDACT_TOKEN} (repeatable); the checkout's path, $HOME "
                        "and /run/user/N/ are always redacted to <repo>, <HOME>, /run/user/<UID>/ instead")
    rc.add_common_flags(s, "systemd unit U", "systemd-user-<U>-<InvocationID>-<host label>")
    s.add_argument("command", nargs="*", metavar="-- CMD", help="with --run: the command the unit runs")
    s.set_defaults(fn=cmd_receipt_systemd)
