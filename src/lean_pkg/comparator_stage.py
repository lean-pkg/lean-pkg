# comparator_stage -- how comparator-receipt's stage 04 runs its judges (#39).
"""Judges stream, never buffer.

A judge (`verify-comparator.sh` -> `lake comparator`, or `comparator-check.sh`) runs with stdin
from /dev/null, stdout and stderr merged into ONE pipe that a reader thread drains line by line
(so neither side can fill a pipe and stall), every line appended and flushed to the unit's log and
to the stage log the moment it arrives, and the judge in its own process group so a TERM to the
runner kills the whole judge tree instead of orphaning it. Lean's `IO.println` is fully buffered
on a pipe, so the command is wrapped in `stdbuf -oL -eL` when stdbuf exists (it reaches lake and
every unsandboxed descendant through the environment), and PYTHONUNBUFFERED=1 covers Python
helpers. While a judge is silent the pool writes a heartbeat line every COMPARATOR_HEARTBEAT_SECONDS
(default 60) naming its run time, its silence and the bytes in its private TMPDIR (where
`lake comparator` writes its NDJSON exports, the dominant phase of a long run).

Before #39 the toolchain judge ran under `subprocess.run(capture_output=True)` and its output
reached the log only after it exited: a run killed by an external timeout left an empty log.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import threading
import time

HEARTBEAT_ENV = "COMPARATOR_HEARTBEAT_SECONDS"
DEFAULT_HEARTBEAT = 60.0
KILL_GRACE = 10.0          # seconds between TERM and KILL for a judge's process group


# ---------------------------------------------------------------------------
# Redaction (#41): absolute paths under the checkout, HOME and TMPDIR never reach a stage log, a
# receipt log or a receipt field; they become <checkout>/..., <home>/..., <tmp>/... . Hashes and
# bindings cover the tree's bytes, not where it was checked out, so nothing they bind changes.

_REDACT: list[tuple[re.Pattern, str]] = []
_PATH_TAIL = r"(?![A-Za-z0-9_.\-])"     # the prefix must end at a path boundary (/home/u, not /home/u2)


def set_redactions(checkout: pathlib.Path | None, home: str | None = None, tmp: str | None = None) -> None:
    """Configure redaction for this process: longest prefix first, real and as-given spellings."""
    import tempfile
    pairs: list[tuple[str, str]] = []
    for value, tag in ((str(checkout) if checkout else None, "<checkout>"),
                       (tmp if tmp is not None else (os.environ.get("TMPDIR") or tempfile.gettempdir()), "<tmp>"),
                       (home if home is not None else os.environ.get("HOME"), "<home>")):
        if not value:
            continue
        for v in {value.rstrip("/"), os.path.realpath(value).rstrip("/")}:
            if len(v) > 1:
                pairs.append((v, tag))
    pairs.sort(key=lambda p: -len(p[0]))
    _REDACT[:] = [(re.compile(re.escape(v) + _PATH_TAIL), tag) for v, tag in pairs]


def redact(text: str) -> str:
    for rx, tag in _REDACT:
        text = rx.sub(tag, text)
    return text


def redact_bytes(data: bytes) -> bytes:
    return redact(data.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape") \
        if _REDACT else data


def redact_json(obj):
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_json(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_json(v) for k, v in obj.items()}
    return obj


class Interrupted(Exception):
    """The runner received TERM/INT/HUP while judges were running."""

    def __init__(self, signum: int):
        super().__init__(f"interrupted by {signal.Signals(signum).name}")
        self.signum = signum


def heartbeat_seconds() -> float:
    try:
        v = float(os.environ.get(HEARTBEAT_ENV) or DEFAULT_HEARTBEAT)
    except ValueError:
        v = DEFAULT_HEARTBEAT
    return max(v, 0.05)


def line_buffered(cmd: list[str]) -> list[str]:
    """cmd under `stdbuf -oL -eL` when stdbuf is on PATH (C-stdio and Lean output then flushes per line)."""
    sb = shutil.which("stdbuf")
    return [sb, "-oL", "-eL", *cmd] if sb else list(cmd)


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def dir_bytes(d: pathlib.Path | None) -> tuple[int, int]:
    """(total bytes, file count) under d; (0, 0) when absent."""
    total = count = 0
    if d is None or not d.is_dir():
        return 0, 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
                count += 1
            except OSError:
                pass
    return total, count


class StageLog:
    """The stage log, shared by every judge thread: each write is one locked, flushed append."""

    def __init__(self, path: pathlib.Path, echo=None):
        self.path, self.echo = path, echo
        self.lock = threading.Lock()
        self.fh = open(path, "ab", buffering=0)

    def write(self, text: str | bytes) -> None:
        data = text.encode("utf-8", "replace") if isinstance(text, str) else text
        data = redact_bytes(data)
        if not data.endswith(b"\n"):
            data += b"\n"
        with self.lock:
            self.fh.write(data)
            if self.echo is not None:
                try:
                    self.echo.write(data.decode("utf-8", "replace"))
                    self.echo.flush()
                except (OSError, ValueError):
                    pass

    def close(self) -> None:
        self.fh.close()


class Judge:
    """One judge process; see the module docstring for the wiring."""

    def __init__(self, cmd: list[str], cwd: pathlib.Path, env: dict, log: pathlib.Path | None,
                 stage: StageLog, label: str, prefix: str = "", tmpdir: pathlib.Path | None = None,
                 preexec=None):
        self.cmd, self.cwd, self.env, self.log = cmd, cwd, dict(env), log
        self.preexec = preexec            # run in the child before exec (the stack limit, #52), or None
        self.stage, self.label, self.prefix, self.tmpdir = stage, label, prefix, tmpdir
        self.proc: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self.started = self.last_output = self.last_beat = 0.0
        self.returncode: int | None = None
        self.killed = False
        self.last_line = ""

    def start(self) -> None:
        self.env["PYTHONUNBUFFERED"] = "1"
        if self.tmpdir is not None:
            self.tmpdir.mkdir(parents=True, exist_ok=True)
            self.env["TMPDIR"] = str(self.tmpdir)
        self.logfh = None                 # log None: the stage log is the only sink (setup stages)
        if self.log is not None:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            self.logfh = open(self.log, "ab", buffering=0)   # copied into .lean-receipts: no paths added
        self.stage.write(self.prefix + "$ " + " ".join(self.cmd))
        self.started = self.last_output = self.last_beat = time.monotonic()
        try:
            self.proc = subprocess.Popen(line_buffered(self.cmd), cwd=self.cwd, env=self.env,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, start_new_session=True, bufsize=0,
                                         preexec_fn=self.preexec)
        except OSError as e:
            msg = f"error: cannot run {self.cmd[0]}: {e}"
            if self.logfh is not None:
                self.logfh.write(redact_bytes((msg + "\n").encode()))
                self.logfh.close()
            self.stage.write(self.prefix + msg)
            self.returncode = 127
            return
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()

    def _pump(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for raw in iter(self.proc.stdout.readline, b""):
            self.last_output = time.monotonic()
            raw = redact_bytes(raw)
            if self.logfh is not None:
                self.logfh.write(raw)
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.strip():
                self.last_line = line.strip()[:160]
            self.stage.write(self.prefix + line)
        self.proc.stdout.close()

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        assert self.proc is not None
        rc = self.proc.poll()
        if rc is None:
            return None
        if self.reader is not None:
            self.reader.join()
        if self.logfh is not None:
            self.logfh.close()
        self.returncode = rc
        return rc

    def beat(self, every: float) -> None:
        now = time.monotonic()
        if now - self.last_output < every or now - self.last_beat < every:
            return
        self.last_beat = now
        size, n = dir_bytes(self.tmpdir)
        tmp = f"; judge temp files {human_bytes(size)} in {n}" if n else ""
        last = f'; last line: "{self.last_line}"' if self.last_line else ""
        self.stage.write(f"heartbeat: {self.label} running {now - self.started:.0f}s, "
                         f"judge silent {now - self.last_output:.0f}s{tmp}{last}")

    def kill(self) -> None:
        """TERM the judge's process group, KILL it after KILL_GRACE seconds."""
        if self.proc is None or self.returncode is not None:
            return
        self.killed = True
        for sig, grace in ((signal.SIGTERM, KILL_GRACE), (signal.SIGKILL, 5.0)):
            try:
                os.killpg(self.proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                self.proc.wait(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        self.poll()

    def elapsed(self) -> float:
        return time.monotonic() - self.started


class SignalGuard:
    """Turns TERM/INT/HUP into Interrupted in the main thread for the duration of a with-block."""

    SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)

    def __enter__(self):
        self.old = {}
        for s in self.SIGNALS:
            try:
                self.old[s] = signal.signal(s, self._raise)
            except (ValueError, OSError):   # not the main thread: leave the defaults alone
                pass
        return self

    @staticmethod
    def _raise(signum, _frame):
        raise Interrupted(signum)

    def __exit__(self, *exc):
        for s, h in self.old.items():
            try:
                signal.signal(s, h)
            except (ValueError, OSError):
                pass
        return False


def run_pool(jobs: list, width: int, start, finish, poll_interval: float = 0.2) -> None:
    """Run jobs, at most `width` at a time. start(job) -> Judge (already started); finish(job, judge)
    -> bool (False stops launching new jobs and kills the running ones). Heartbeats while waiting.
    On Interrupted (or any exception) every running judge's process group is killed, then it re-raises."""
    every = heartbeat_seconds()
    pending = list(jobs)
    running: list[tuple[object, Judge]] = []
    stop = False
    try:
        while pending or running:
            while pending and not stop and len(running) < max(1, width):
                job = pending.pop(0)
                running.append((job, start(job)))
            if stop and not running:
                break
            done = [(j, jd) for j, jd in running if jd.poll() is not None]
            for j, jd in done:
                running.remove((j, jd))
                if not finish(j, jd):
                    stop = True
            if stop:
                for _j, jd in running:
                    jd.kill()
                for j, jd in running:
                    finish(j, jd)
                running = []
                break
            if not done:
                for _j, jd in running:
                    jd.beat(every)
                time.sleep(min(poll_interval, every))
    except BaseException:
        for _j, jd in running:
            jd.kill()
        raise


# ---------------------------------------------------------------------------
# Slots and units. A slot is one theorem_names entry of one configuration, numbered 1..N across
# the configurations in order. A unit is what one judge invocation judges: a configuration (or a
# subset of its slots). Progress lines name slots; 04-comparator.partial.json names the slots done,
# pending and failed, rewritten after every unit so even a SIGKILL leaves it current.

PARTIAL_FILE = "04-comparator.partial.json"
PARTIAL_SCHEMA = "comparator-partial/1"
UNITS_DIR = "04-units"


@dataclasses.dataclass(frozen=True)
class Slot:
    index: int          # 1-based, global over the configurations in order
    config: str
    name: str

    def row(self) -> dict:
        return {"slot": self.index, "config": self.config, "name": self.name}


@dataclasses.dataclass
class Unit:
    n: int
    config: str                     # "" for a judge that takes every configuration at once (legacy)
    slots: list
    whole: bool                     # judges the configuration file itself, not a partial copy
    id: str = ""

    def label(self) -> str:
        if len(self.slots) == 1:
            return self.slots[0].name
        return f"{self.config or 'all configs'}[{len(self.slots)} names]"


def list_slots(cfgs: list[tuple[str, dict]]) -> list[Slot]:
    out: list[Slot] = []
    for rel, cfg in cfgs:
        for name in cfg.get("theorem_names") or []:
            out.append(Slot(len(out) + 1, rel, str(name)))
    return out


def ranges(indices) -> str:
    """[1,2,3,7,9,10] -> '1-3,7,9-10'."""
    xs = sorted(set(indices))
    parts, i = [], 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[j] + 1:
            j += 1
        parts.append(str(xs[i]) if i == j else f"{xs[i]}-{xs[j]}")
        i = j + 1
    return ",".join(parts)


def unit_id(binding: dict, unit: Unit) -> str:
    key = [binding.get("commit"), binding.get("config_hash"), unit.config, unit.whole,
           [s.name for s in unit.slots]]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]


class Progress:
    """Slots done / pending / failed for one stage-04 run, mirrored to the partial report."""

    def __init__(self, path: pathlib.Path, slots: list[Slot], meta: dict):
        self.path, self.slots, self.meta = path, slots, dict(meta)
        self.done: dict[int, str] = {}      # slot index -> how ("judged" | "cached")
        self.failed: set[int] = set()
        self.state = "running"

    def mark(self, slots, how: str) -> None:
        for s in slots:
            if how == "failed":
                self.failed.add(s.index)
            else:
                self.done[s.index] = how
                self.failed.discard(s.index)

    def counts(self) -> dict:
        return {"done": len(self.done), "total": len(self.slots), "failed": len(self.failed)}

    def write(self) -> None:
        pend = [s.row() for s in self.slots if s.index not in self.done and s.index not in self.failed]
        data = {"schema": PARTIAL_SCHEMA, **self.meta, "state": self.state, **self.counts(),
                "slots_done": [{**s.row(), "how": self.done[s.index]} for s in self.slots
                               if s.index in self.done],
                "slots_pending": pend,
                "slots_failed": [s.row() for s in self.slots if s.index in self.failed],
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def summary(self) -> str:
        c = self.counts()
        return f"partial: {c['done']}/{c['total']} done" + (f", {c['failed']} failed" if c["failed"] else "")


# ---------------------------------------------------------------------------
# Resume (#39): a slot judged and accepted is cached under 04-slots/<slot-hash>.json, keyed by
# everything that decides its verdict: commit, config hash (every configuration's path and bytes),
# lean-toolchain, mechanism, the judge script's digest, the configuration path and the slot name.
# The unit log that accepted it is kept content-addressed in 04-slots/logs/ and re-checked (digest
# and PASS line) before a cached slot is trusted; anything missing or different is judged again.

SLOTS_DIR = "04-slots"
SLOT_SCHEMA = "comparator-slot/1"


def slot_key(binding: dict, mechanism: str, judge_sha256: str, slot: Slot) -> dict:
    return {"commit": binding.get("commit"), "config_hash": binding.get("config_hash"),
            "lean_toolchain": binding.get("lean_toolchain"), "mechanism": mechanism,
            "judge_sha256": judge_sha256, "config": slot.config, "name": slot.name}


def slot_hash(key: dict) -> str:
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]


class SlotCache:
    def __init__(self, root: pathlib.Path, binding: dict, mechanism: str, judge_sha256: str, accept):
        self.root, self.binding, self.mechanism, self.judge = root, binding, mechanism, judge_sha256
        self.accept = accept                      # accept(log_text) -> bool: the PASS predicate

    def path(self, slot: Slot) -> pathlib.Path:
        return self.root / f"{slot_hash(slot_key(self.binding, self.mechanism, self.judge, slot))}.json"

    def get(self, slot: Slot) -> dict | None:
        """The cache entry for slot when it is valid for this run, else None."""
        try:
            e = json.loads(self.path(slot).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(e, dict) or e.get("schema") != SLOT_SCHEMA or e.get("result") != "pass" \
                or e.get("key") != slot_key(self.binding, self.mechanism, self.judge, slot):
            return None
        log = self.root / str(e.get("log") or "")
        try:
            data = log.read_bytes()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != e.get("log_sha256") \
                or not self.accept(data.decode("utf-8", "replace")):
            return None
        e["log_path"] = log
        return e

    def put(self, slots: list[Slot], log: pathlib.Path, whole: bool, wall: float) -> None:
        data = log.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        logs = self.root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        dst = logs / f"{digest}.log"
        if not dst.exists():
            tmp = dst.with_name(dst.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dst)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for s in slots:
            entry = {"schema": SLOT_SCHEMA, "key": slot_key(self.binding, self.mechanism, self.judge, s),
                     "result": "pass", "slot": s.index, "unit_slots": [x.name for x in slots],
                     "whole": whole, "log": f"logs/{digest}.log", "log_sha256": digest,
                     "wall_s": round(wall, 3), "judged_at": now}
            p = self.path(s)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, p)


JOBS_ENV = "LEAN_PKG_COMPARATOR_JOBS"


def shards(slots: list, n: int) -> list[list]:
    """slots in min(n, len) contiguous chunks whose sizes differ by at most one (neighbouring
    theorems usually share most of their export closure, so contiguity keeps shards cheaper)."""
    k = max(1, min(n, len(slots)))
    q, r = divmod(len(slots), k)
    out, i = [], 0
    for c in range(k):
        size = q + (1 if c < r else 0)
        out.append(slots[i:i + size])
        i += size
    return out


# ---------------------------------------------------------------------------
# Slot subsets (--slots SPEC) and the stratified sample that --estimate and `sample:K` share.

def namespace(name: str) -> str:
    return name.rsplit(".", 1)[0] if "." in name else ""


def stratified_sample(slots: list[Slot], k: int) -> list[Slot]:
    """k slots spread across strata (configuration, namespace of the name: the closest proxy for the
    module that the configuration itself carries): round-robin over the strata in order of first
    appearance, each stratum yielding its slots in order. Deterministic; returned in slot order."""
    strata: dict[tuple[str, str], list[Slot]] = {}
    for s in slots:
        strata.setdefault((s.config, namespace(s.name)), []).append(s)
    queues = [list(v) for v in strata.values()]
    out: list[Slot] = []
    while len(out) < min(k, len(slots)):
        for q in queues:
            if q and len(out) < k:
                out.append(q.pop(0))
    return sorted(out, key=lambda s: s.index)


def parse_slot_spec(spec: str, slots: list[Slot]) -> list[Slot]:
    """SPEC: comma-separated tokens, each `sample:K`, an index `i` or range `i-j` (1-based, over all
    configurations in order), a slot name, or `<config>:<name>`. Raises ValueError naming the token."""
    picked: dict[int, Slot] = {}
    by_name: dict[str, list[Slot]] = {}
    for s in slots:
        by_name.setdefault(s.name, []).append(s)
    for tok in [t.strip() for t in spec.split(",")]:
        if not tok:
            continue
        if tok.startswith("sample:"):
            try:
                k = int(tok[7:])
            except ValueError:
                k = 0
            if k < 1:
                raise ValueError(f"{tok!r}: sample:K needs a positive integer K")
            for s in stratified_sample(slots, k):
                picked[s.index] = s
        elif tok.replace("-", "").isdigit() and tok.count("-") <= 1 and not tok.startswith("-"):
            a, _, b = tok.partition("-")
            lo, hi = int(a), int(b or a)
            if not (1 <= lo <= hi <= len(slots)):
                raise ValueError(f"{tok!r}: slot indices run 1..{len(slots)}")
            for s in slots[lo - 1:hi]:
                picked[s.index] = s
        else:
            cfg, sep, name = tok.rpartition(":") if ":" in tok else ("", "", tok)
            hits = [s for s in by_name.get(name, []) if not sep or s.config == cfg]
            if not hits:
                raise ValueError(f"{tok!r}: no such slot (theorem_names entry)")
            if len(hits) > 1:
                raise ValueError(f"{tok!r} names a slot in several configurations "
                                 f"({', '.join(s.config for s in hits)}); write <config>:{name}")
            picked[hits[0].index] = hits[0]
    if not picked:
        raise ValueError("selects no slot")
    return [picked[i] for i in sorted(picked)]
