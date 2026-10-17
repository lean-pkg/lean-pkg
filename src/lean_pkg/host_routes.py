#!/usr/bin/env python3
"""`lean-pkg comparator host-check --routes [--json]` (field report #5): host and cluster planning before sbatch.

Per route, whether this host (run it inside the job's image, with the job's binds and PATH) supports it and
exactly why:

- judge: the official `lake comparator` judge, sandboxed in bwrap; needs an unprivileged user namespace.
  The same requirement set as `mechanism toolchain` of `comparator host-check` (one source: host_checks()).
- prepare: the setup stages only (`comparator-receipt --prepare`: dependency checkout, Mathlib cache, build);
  no sandbox, so it may run in a cluster job.
- transfer: build here, judge on another host that passes the judge route: prepare here, pack and hash the
  build and its evidence directory, copy them to a checkout of the same commit there, and check the copied
  oleans with `comparator-receipt --verify-oleans` (#173) before judging.

Plus the facts a job needs: executables resolved like `type -P` (the Slurm template's required-tool list is
read from SBATCH_TEMPLATE itself, so there is one list), locale (the inherited-locale warning that broke a
strict empty stderr), open-file limits with the LEAN_NUM_THREADS advice, the CPU affinity count and the
cgroup memory limit. Read-only: no network, no writes, no registry. It never offers an unsandboxed judge:
a host without a working bwrap has no judge route, only prepare and transfer.
"""

from __future__ import annotations

import os
import pathlib
import re
import stat
import sys

ROUTES_SCHEMA = "host-routes/1"
ROUTES = ("judge", "prepare", "transfer")
# comparator-receipt is a Python program; its setup stages run lake, lean and git.
PREPARE_TOOLS = ("lake", "lean", "git", "python3")
# The transfer packs and hashes .lake/build for the copy to the judging host.
TRANSFER_TOOLS = ("tar", "sha256sum")
# Always reported, in this order; then the Slurm template's own required tools not already listed.
CORE_EXECUTABLES = ("lake", "lean", "git", "python3", "bwrap", "sha256sum", "tar", "time")
TIME_NOTE = ("`time` is a bash keyword, which `type -P` never reports: only a time binary resolves. The "
             "keyword works in a bash script; `$RUN time ...`, `env time`, `xargs time` or `srun time` need "
             "the binary, which many images lack")

NOFILE_LOW = 4096               # a soft `ulimit -n` below this is tight for a Mathlib-heavy Lean build
FILE_TABLE_FREE_LOW = 262144    # free entries in the system file table (file-nr max - allocated)
MATHLIB_THREADS = 3             # the Slurm template's LEAN_NUM_THREADS default
CGROUP_UNLIMITED = 1 << 62      # cgroup v1 reports "no limit" as a page-rounded 2**63-1
CGROUP_ROOT = pathlib.Path("/sys/fs/cgroup")
PROC_CGROUP = pathlib.Path("/proc/self/cgroup")
FILE_NR = pathlib.Path("/proc/sys/fs/file-nr")
MEMINFO = pathlib.Path("/proc/meminfo")
SLURM_ENV = ("SLURM_JOB_ID", "SLURM_CPUS_PER_TASK", "SLURM_CPUS_ON_NODE", "SLURM_MEM_PER_NODE",
             "SLURM_MEM_PER_CPU")
LOCALE_VARS = ("LC_ALL", "LC_CTYPE", "LANG")


# ---------------------------------------------------------------------------
# One source of truth with `receipt slurm --template` (#172): its required host tools and its locale line.

def template_host_tools() -> tuple[str, ...]:
    """The HOST_TOOLS list of the Slurm template (`receipt slurm --template`), without EXTRA_TOOLS."""
    from .receipt import SBATCH_TEMPLATE
    m = re.search(r'^HOST_TOOLS="([^"]*)"', SBATCH_TEMPLATE, re.MULTILINE)
    if m is None:   # the template changed shape: tests/test_host_routes.py fails on this
        return ()
    return tuple(w for w in m.group(1).split() if not w.startswith("$"))


def template_locale() -> str:
    """The template's locale export, e.g. 'LC_ALL=C LANG=C PYTHONUTF8=1'."""
    from .receipt import SBATCH_TEMPLATE
    m = re.search(r"^export (LC_ALL=\S+ LANG=\S+ PYTHONUTF8=\S+)\s*$", SBATCH_TEMPLATE, re.MULTILINE)
    return m.group(1) if m else "LC_ALL=C LANG=C PYTHONUTF8=1"


# ---------------------------------------------------------------------------
# Probes. Each is a module-level function so the tests monkeypatch it; none writes anything.

def type_p(name: str, path: str | None = None) -> str | None:
    """Like bash `type -P NAME`: the first PATH entry holding an executable regular file NAME (an empty
    entry is the current directory). Keywords, builtins, aliases and functions are never reported."""
    path = os.environ.get("PATH", "") if path is None else path
    for d in path.split(os.pathsep):
        p = os.path.join(d or ".", name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and os.access(p, os.X_OK):
            return p
    return None


def locale_available(name: str) -> bool:
    """Whether the C library can load locale NAME (what bash's setlocale warning is about). The process's
    LC_CTYPE is restored before returning."""
    import locale
    old = locale.setlocale(locale.LC_CTYPE)
    try:
        locale.setlocale(locale.LC_CTYPE, name)
        return True
    except locale.Error:
        return False
    finally:
        locale.setlocale(locale.LC_CTYPE, old)


def nofile_limits() -> tuple[int | None, int | None]:
    """(soft, hard) of RLIMIT_NOFILE; None means unlimited or unknown."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ImportError, OSError, ValueError):
        return None, None
    inf = getattr(__import__("resource"), "RLIM_INFINITY", -1)
    return (None if soft == inf else int(soft)), (None if hard == inf else int(hard))


def read_text(path: pathlib.Path) -> str | None:
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def cpus_allowed() -> int | None:
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count()


def env_get(name: str) -> str | None:
    return os.environ.get(name)


# ---------------------------------------------------------------------------
# Parsers (pure; tested on fixed text)

def parse_file_nr(text: str | None) -> dict | None:
    """/proc/sys/fs/file-nr 'allocated unused max' -> {allocated, unused, max, free}."""
    if not text:
        return None
    parts = text.split()
    if len(parts) < 3 or not all(p.isdigit() for p in parts[:3]):
        return None
    alloc, unused, mx = (int(p) for p in parts[:3])
    return {"allocated": alloc, "unused": unused, "max": mx, "free": max(mx - alloc, 0)}


def _limit_walk(base: pathlib.Path, rel: str, filename: str, unlimited) -> int | None:
    """The tightest FILENAME limit from base/rel up to base (a parent's limit binds its children)."""
    rel = rel.strip().lstrip("/")
    p = base / rel if rel else base
    found: list[int] = []
    while True:
        v = read_text(p / filename)
        if v is not None:
            v = v.strip()
            if v.isdigit() and not unlimited(int(v)):
                found.append(int(v))
        if p == base or base not in p.parents:
            break
        p = p.parent
    return min(found) if found else None


def cgroup_memory(proc_cgroup: str | None, root: pathlib.Path = CGROUP_ROOT) -> dict:
    """{version: 2|1|None, path, limit_bytes (None = no limit found)} from /proc/self/cgroup text.
    v2: the unified line `0::/path`, memory.max ('max' = none). v1: the line whose controllers include
    `memory`, memory.limit_in_bytes under <root>/memory (2**62 or more = none)."""
    if not proc_cgroup:
        return {"version": None, "path": None, "limit_bytes": None}
    v1 = v2 = None
    for line in proc_cgroup.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        hid, ctrls, path = parts
        if hid == "0" and ctrls == "":
            v2 = path
        elif "memory" in ctrls.split(","):
            v1 = path
    if v1 is not None:
        return {"version": 1, "path": v1,
                "limit_bytes": _limit_walk(root / "memory", v1, "memory.limit_in_bytes",
                                           lambda n: n >= CGROUP_UNLIMITED)}
    if v2 is not None:
        return {"version": 2, "path": v2,
                "limit_bytes": _limit_walk(root, v2, "memory.max", lambda n: False)}
    return {"version": None, "path": None, "limit_bytes": None}


def mem_available(meminfo: str | None) -> int | None:
    m = re.search(r"^MemAvailable:\s+(\d+)\s*kB", meminfo or "", re.MULTILINE)
    return int(m.group(1)) * 1024 if m else None


def _gib(n: int | None) -> str:
    return "none" if n is None else f"{n / (1 << 30):.1f} GiB"


# ---------------------------------------------------------------------------

def locale_report() -> dict:
    vals = {v: env_get(v) for v in LOCALE_VARS}
    c_utf8 = locale_available("C.UTF-8")
    missing = sorted({x for x in vals.values() if x and x not in ("C", "POSIX") and not locale_available(x)})
    fix = template_locale()
    if missing:
        level = "warn"
        detail = (f"{', '.join(missing)} is set but not installed here: bash and other tools print a setlocale "
                  f"warning on stderr, and a lean-pkg receipt refuses a non-empty stderr (it is never "
                  f"filtered). Export {fix} in the job before any tool runs (the `receipt slurm --template` "
                  f"wrapper does)")
    else:
        level = "ok"
        detail = "every set locale variable names an installed locale"
    return {"LANG": vals["LANG"], "LC_ALL": vals["LC_ALL"], "LC_CTYPE": vals["LC_CTYPE"], "c_utf8": c_utf8,
            "unavailable": missing, "level": level, "detail": detail, "job_export": fix}


def limits_report() -> dict:
    soft, hard = nofile_limits()
    fnr = parse_file_nr(read_text(FILE_NR))
    reasons = []
    if soft is not None and soft < NOFILE_LOW:
        reasons.append(f"ulimit -n soft {soft} < {NOFILE_LOW}")
    if fnr is not None and fnr["free"] < FILE_TABLE_FREE_LOW:
        reasons.append(f"system file table: {fnr['free']} free of {fnr['max']} (/proc/sys/fs/file-nr)")
    return {"nofile_soft": soft, "nofile_hard": hard, "file_nr": fnr, "tight": bool(reasons),
            "reasons": reasons, "level": "warn" if reasons else "ok"}


def resources_report() -> dict:
    cg = cgroup_memory(read_text(PROC_CGROUP))
    slurm = {k: env_get(k) for k in SLURM_ENV if env_get(k) is not None}
    return {"cpus_allowed": cpus_allowed(), "cgroup_version": cg["version"],
            "cgroup_memory_max": cg["limit_bytes"], "mem_available": mem_available(read_text(MEMINFO)),
            "slurm": slurm}


def advice_list(limits: dict, resources: dict, loc: dict) -> list[str]:
    out = []
    cpus = resources["cpus_allowed"]
    if limits["tight"]:
        line = (f"file limits are tight ({'; '.join(limits['reasons'])}): Mathlib-heavy packages: "
                f"LEAN_NUM_THREADS={MATHLIB_THREADS}")
        if limits["nofile_soft"] is not None and (limits["nofile_hard"] is None
                                                  or limits["nofile_hard"] > limits["nofile_soft"]):
            line += f"; raise the soft limit first: ulimit -n {limits['nofile_hard'] or 'unlimited'}"
        out.append(line + ". \"Too many open files in system\" means fewer threads, never excluding the node")
    else:
        out.append(f"LEAN_NUM_THREADS at most {cpus or 'the allowed CPUs'}; Mathlib-heavy packages: "
                   f"{MATHLIB_THREADS} if a build reports \"Too many open files\"")
    if cpus is not None:
        out.append(f"size the job to measured use: {cpus} CPU(s) allowed here (sched_getaffinity); about "
                   f"--cpus-per-task=4 for a serial checker; read TotalCPU/Elapsed of a pilot in sacct")
    if resources["cgroup_memory_max"] is not None:
        out.append(f"cgroup memory limit {_gib(resources['cgroup_memory_max'])}: size the judge's memory cap "
                   f"(`comparator-receipt --estimate`) below it")
    if loc["level"] != "ok":
        out.append(f"export {loc['job_export']} before any tool runs")
    return out


def _route(name, ok, needs, failing, reason, commands, notes=()):
    return {"route": name, "supported": ok, "needs": list(needs), "failing": list(failing),
            "reason": reason, "commands": list(commands) if ok else [], "notes": list(notes)}


def routes_report(hc: dict) -> dict:
    """The additive `--routes` keys for a host_checks() result."""
    from .comparator_receipt import ALLOWED_HOSTS_ENV, BUILD_SOURCE_ENV, DEFAULT_EVIDENCE, TOOLCHAIN_FLOOR
    tmpl = template_host_tools()
    names = list(CORE_EXECUTABLES) + [t for t in tmpl if t not in CORE_EXECUTABLES]
    exes = {n: type_p(n) for n in names}
    loc, lim, res = locale_report(), limits_report(), resources_report()
    threads = f"LEAN_NUM_THREADS={MATHLIB_THREADS} " if lim["tight"] else ""
    kernel = next((c for c in hc["checks"] if c["item"] == "kernel"), {"level": "ok", "detail": ""})
    common_notes = [f"locale: {loc['detail']}"] if loc["level"] != "ok" else []

    jd = hc["mechanism_detail"]["toolchain"]
    policy_note = (f"host policy: inside a Slurm job comparator-receipt runs as is; on a workstation without Slurm "
                   f"and with {ALLOWED_HOSTS_ENV} unset it runs as is too (the workstation default); on a Slurm "
                   f"login node list this host in {ALLOWED_HOSTS_ENV}")
    if jd["ok"]:
        judge = _route("judge", True, jd["needs"], [], (
            f"bwrap creates a user namespace and {', '.join(n for n in jd['needs'] if n not in ('kernel', 'bwrap'))} "
            f"resolve; the project's toolchain must be Lean {TOOLCHAIN_FLOOR}+ (comparator-receipt checks it "
            f"per project)"),
            [f"cd CHECKOUT && {threads}comparator-receipt --prepare",
             "comparator-receipt --estimate --json   # read recommended_cap",
             f"timeout \"$CAP\" comparator-receipt --from-prepared {DEFAULT_EVIDENCE.as_posix()}"],
            common_notes + [policy_note])
    else:
        judge = _route("judge", False, jd["needs"], jd["failing"], (
            "; ".join(jd["failing"]) + ". The judge runs only inside bwrap; there is no unsandboxed route. "
            "Build here and judge on a host whose `host-check --routes` says judge: yes (route transfer)"),
            [])

    def missing(tools):
        return [f"{t}: not found (type -P)" for t in tools if exes[t] is None]

    pf = ([f"kernel: {kernel['detail']}"] if kernel["level"] == "fail" else []) + missing(PREPARE_TOOLS)
    prepare = _route("prepare", not pf, ("kernel",) + PREPARE_TOOLS, pf,
                     ("; ".join(pf) if pf else
                      f"{', '.join(PREPARE_TOOLS)} resolve; the setup stages (dependency checkout, Mathlib cache, "
                      f"build) need no sandbox, so they may run in a cluster job"),
                     [f"cd CHECKOUT && {threads}comparator-receipt --prepare"], common_notes)
    tf = pf + missing(TRANSFER_TOOLS)
    t_notes = list(common_notes)
    if judge["supported"]:
        t_notes.append("this host can judge itself; the transfer is needed only to judge elsewhere")
    transfer = _route("transfer", not tf, ("kernel",) + PREPARE_TOOLS + TRANSFER_TOOLS, tf,
                      ("; ".join(tf) if tf else
                       "this host can be the build side: prepare here, pack .lake/build and the evidence "
                       "directory with their sha256, copy them to a checkout of the same commit (same "
                       "lean-toolchain and lake-manifest.json) on a host whose `host-check --routes` says "
                       "judge: yes, check the copied oleans there with `comparator-receipt --verify-oleans`, "
                       "and judge there"),
                      [f"cd CHECKOUT && {threads}comparator-receipt --prepare",
                       f"tar -cf build.tar .lake/build && tar -cf evidence.tar -C {DEFAULT_EVIDENCE.parent.as_posix()} "
                       f"{DEFAULT_EVIDENCE.name} && sha256sum build.tar evidence.tar > transfer.sha256",
                       "# judging host, a checkout of the same commit, after copying the three files:",
                       "sha256sum -c transfer.sha256 && tar -xf build.tar && mkdir -p \"$CLUSTER_EVIDENCE\" "
                       "&& tar -xf evidence.tar -C \"$CLUSTER_EVIDENCE\"",
                       "lean-pkg comparator host-check --routes   # judge: yes required there",
                       f"export {BUILD_SOURCE_ENV}=\"cluster job $JOB\"",
                       f"comparator-receipt --verify-oleans \"$CLUSTER_EVIDENCE/{DEFAULT_EVIDENCE.name}\"   "
                       f"# the copied oleans are the job's",
                       "comparator-receipt --prepare   # compiles nothing: build: prebuilt",
                       "comparator-receipt --estimate --json   # read recommended_cap",
                       "timeout \"$CAP\" comparator-receipt"],
                      t_notes)
    exe_rows = {n: {"path": p, "level": "ok" if p else ("warn" if n in ("time", "bwrap") else "fail"),
                    **({"note": TIME_NOTE} if n == "time" else {}),
                    "required_by": sorted(
                        ({"judge"} if n in jd["needs"] else set())
                        | ({"prepare", "transfer"} if n in PREPARE_TOOLS else set())
                        | ({"transfer"} if n in TRANSFER_TOOLS else set())
                        | ({"slurm-template"} if n in tmpl else set()))}
                for n, p in exes.items()}
    return {"routes_schema": ROUTES_SCHEMA, "routes": [judge, prepare, transfer], "executables": exe_rows,
            "locale": loc, "limits": lim, "resources": res, "advice": advice_list(lim, res, loc)}


def render_text(rep: dict) -> list[str]:
    out = []
    for r in rep["routes"]:
        out.append(f"route {r['route']}: {'yes' if r['supported'] else 'no'} ({r['reason']})")
        for c in r["commands"]:
            out.append(f"  run: {c}")
        for n in r["notes"]:
            out.append(f"  note: {n}")
    for n, e in rep["executables"].items():
        out.append(f"{e['level']} exe {n}: {e['path'] or 'not found (type -P)'}"
                   + (f" [{', '.join(e['required_by'])}]" if e["required_by"] else "")
                   + (f"; {e['note']}" if e.get("note") and not e["path"] else ""))
    loc = rep["locale"]
    out.append(f"{loc['level']} locale: LANG={loc['LANG'] or '(unset)'} LC_ALL={loc['LC_ALL'] or '(unset)'}; "
               f"C.UTF-8 installed: {'yes' if loc['c_utf8'] else 'no'}; {loc['detail']}")
    lim = rep["limits"]
    fnr = lim["file_nr"]
    out.append(f"{lim['level']} limits: ulimit -n soft {lim['nofile_soft'] or 'unlimited'} hard "
               f"{lim['nofile_hard'] or 'unlimited'}; file-nr "
               + (f"{fnr['allocated']} allocated of {fnr['max']}" if fnr else "unreadable"))
    res = rep["resources"]
    out.append(f"ok resources: {res['cpus_allowed']} CPU(s) allowed; cgroup v{res['cgroup_version'] or '?'} "
               f"memory limit {_gib(res['cgroup_memory_max'])}; MemAvailable {_gib(res['mem_available'])}"
               + (f"; {' '.join(f'{k}={v}' for k, v in res['slurm'].items())}" if res["slurm"] else ""))
    for a in rep["advice"]:
        out.append(f"advice: {a}")
    return out


if __name__ == "__main__":   # pragma: no cover
    sys.exit("use: lean-pkg comparator host-check --routes")
