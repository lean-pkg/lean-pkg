# lean-pkg receipt slurm -- a lower-tier receipt from a finished Slurm job
"""`lean-pkg receipt slurm --job J --log PATH --commit SHA [--stdout PATH] [--stderr PATH] [--checker TEXT]
[--repo DIR] [--redact REGEX ...] [--sacct-file F] [--out PATH] [--append-readme [--readme-heading TEXT]
[--readme-line TEXT ...] [--tar-sha256 HEX]] [--json]` (#70): turn a Slurm verification job (its accounting,
its verify log, its stdout and stderr) into a lower-tier receipt in exactly the shape `gate --receipt`
validates. It never reads or writes a registry.
`--template` prints SBATCH_TEMPLATE, a minimal sbatch wrapper that emits every line read below; `--check` (preflight)
runs every check below that the job's files decide, writes nothing, and names every problem (field report).

Checks, all run before anything is written (every failed check is listed; any one refuses, exit 1):

1. Job state: `sacct -j J --format=JobID,State,ExitCode,Elapsed,TotalCPU -P -n` (or that output saved to
   `--sacct-file`, for a host without Slurm; the first three columns suffice, other columns need sacct's
   header line, else `unexpected sacct format`). The main job line (JobID
   equal to J, not a `.batch` or `.extern` step) must say State `COMPLETED` and ExitCode `0:0`.
2. Seal. With `--stdout` (the job's `.out` file): its one `verify-log-sha256` line must equal the SHA-256
   of the raw verify log, and its `pre-manifest-sha256` and `post-manifest-sha256` lines must be equal and
   equal the SHA-256 of `lake-manifest.json` committed at the commit. A stdout line is `KEY VALUE` or
   `KEY: VALUE`. Without `--stdout` (fallback): the verify log itself must hold exactly one
   `verify-log-sha256: <hex>` line equal to the SHA-256 of the log with that line removed; the manifest
   check is skipped and says so.
3. `--stderr` (the job's `.err` file), when given, must be empty.
4. Fields: `lean_toolchain` is `lean-toolchain` committed at the commit and `mathlib_rev` the `mathlib`
   package's rev in `lake-manifest.json` committed there (else the log's `mathlib-rev:`); a log
   `lean-toolchain:` / `mathlib-rev:` line that disagrees refuses. `sorry_count` is the log's
   `sorry-count:` line (refused when the log has more `declaration uses 'sorry'` lines than it declares), else
   the number of `declaration uses 'sorry'` lines, counted only when the log shows the
   build finished (`Build completed successfully` or `lake build: ok`; neither refuses); it must be 0. `axioms` is the
   log's `axioms:` line (refused when it omits an axiom the log's own `#print axioms` output reports), else the
   union of its `#print axioms` output (`depends on axioms: [...]`, `does not depend on any axioms`); none of
   either refuses. Names are compared without a universe suffix (`Classical.choice.{u}` is `Classical.choice`).
   `checker` is `--checker`, else the log's `checker:`.
   Optional log lines `commit:`, `job-id:`, `generated-at:` must agree / parse. A log with no `commit:` line is
   refused without --stdout (the in-log seal binds it to nothing) and WARNed with it (`log_commit_bound: false`).
   Every field but `checker` is read from the RAW log. Without `generated-at:` the
   receipt is stamped with the time of writing; a re-run whose receipt matches the one already written in
   every other field keeps that file's `generated_at` (#85), anything else differing is refused.
5. Redaction: the per-job temporary prefixes (DEFAULT_REDACTIONS) and every `--redact REGEX` are replaced
   in the log text; a redaction that removes or alters a line the parser reads refuses (parse_raw_log). The
   public-safety scanner (plus this machine's user and host names and a bare home directory, `_scanner`) then
   runs over the redacted log, the receipt JSON and the README block. Any blocking hit refuses, naming the
   families only. A committed symlink at the commit resolving into `.lean-receipts/` refuses (gate refuses it).

Output: `job_id` is `slurm-<J>`; the redacted log `<c12>-slurm-<J>-verify.redacted.log` and the receipt
`<c12>-slurm-<J>.json` (the name `gate` stores it under) go to `<repo>/.lean-receipts/` (`--out PATH` names
the receipt, the log goes beside it). `report_sha256` is the SHA-256 of the redacted log as written. A
differing file already at either path is refused. The README entry block is printed, and with
`--append-readme` appended to `README.md` beside the receipt. With `--stdout` the seal evidence is persisted
(#72): the receipt gains the additive keys `job_stdout_sha256` (the job stdout file) and `manifest_sha256`
(`lake-manifest.json` at the commit), which `gate` ignores, and the README block a `- Seal:` line naming both.
Everything after the executor's own terminal-state check (2. to 5. and the output) is `produce`, shared with
`receipt systemd` (receipt_systemd.py), which hands it an `Evidence` with its job identity, check lines, README
lines and extra evidence files.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys

from . import cli as core
from . import public_safety as ps

SHA_KEY = "verify-log-sha256"
PRE_KEY, POST_KEY = "pre-manifest-sha256", "post-manifest-sha256"
LOG_KEYS = ("lean-toolchain", "mathlib-rev", "checker", "sorry-count", "axioms", "commit", "job-id",
            "generated-at")
# (regex, replacement): the per-job temporary account prefix and the scratch user directory a Slurm verify
# script prints (`/tmp/<account>-job-...`, `/tmp/<account>-job123/`, `/scratch/<user>/`).
USER_TOKEN = "<USER>"
DEFAULT_REDACTIONS = (
    (r"/tmp/[^/\s<>]+?-job(?=[-\d])", f"/tmp/{USER_TOKEN}-job"),
    (r"/scratch/[^/\s<>]+/", f"/scratch/{USER_TOKEN}/"),
)
REDACT_TOKEN = "<redacted>"
SACCT_FORMAT = "JobID,State,ExitCode,Elapsed,TotalCPU"
SACCT_TIMEOUT = 60
_KEY_LINE = re.compile(r"^([a-z0-9-]+):[ \t]*(.*?)[ \t]*$")
_SHA_LINE = re.compile(rb"^" + SHA_KEY.encode() + rb":[ \t]*([0-9A-Fa-f]{64})[ \t]*\r?$")
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
# Lean 4 prints ``declaration uses `sorry` `` (backticks, every toolchain from v4.29 on); older or
# re-quoted logs use 'sorry'. Matching only the quoted form left real warnings uncounted (review finding).
_SORRY_RE = re.compile(r"declaration uses ['`]sorry['`]")
# Evidence that the build ran to the end, so the absence of sorry warnings is a measured 0 (#82): Lake's own
# summary line, or the `lake build: ok` line the lab's verify script prints.
_BUILD_DONE_RE = re.compile(r"Build completed successfully|^lake build: ok\b", re.M)
_DEPENDS_RE = re.compile(r"depends on axioms:\s*\[([^\]]*)\]")
_NO_AXIOMS_RE = re.compile(r"does not depend on any axioms")

# `receipt slurm --template` (field report): a minimal sbatch wrapper that emits exactly what the receipt reads,
# so the first long job is receiptable. tests/test_receipt_slurm.py runs it against a fake build.
SBATCH_TEMPLATE = r"""#!/bin/bash
# verify.sbatch: a Lean verification job whose files `lean-pkg receipt slurm` accepts
# (printed by `lean-pkg receipt slurm --template`). Submit it from the package checkout:
#
#   sbatch verify.sbatch
#   lean-pkg receipt slurm --check --log verify-J.log --stdout verify-J.out --stderr verify-J.err --commit SHA
#   lean-pkg receipt slurm --job J --log verify-J.log --stdout verify-J.out --stderr verify-J.err --commit SHA
#
# Run --check on the files of a short trial job before the long one: it names every line still missing.
# What the receipt reads; keep these lines as they are:
#   stdout (.out): exactly one line each of pre-manifest-sha256, post-manifest-sha256, verify-log-sha256
#   stderr (.err): empty, so every tool writes into the log, never to the job's stderr
#   the log: one line each of checker:, sorry-count:, axioms: (and lean-toolchain:, commit:, job-id:,
#            generated-at:, which must agree with the commit and the job)
#   the job: sacct State COMPLETED, ExitCode 0:0 (any failed step fails the job: set -e)
#SBATCH --job-name=lean-verify
#SBATCH --output=verify-%j.out
#SBATCH --error=verify-%j.err
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=02:00:00
# Add the partition, account or QOS lines your site requires.

set -euo pipefail

# The package's own commands (placeholders; edit them here or set them when submitting).
VERIFY_CMD=${VERIFY_CMD:-"lake build"}
# a file of `#print axioms Name` lines for the theorems the receipt covers
AXIOMS_CMD=${AXIOMS_CMD:-"lake env lean Verify/Axioms.lean"}
CHECKER=${CHECKER:-"verify.sbatch (lake build; #print axioms)"}
# Run the tools inside the job container when the node needs one, e.g. RUN="apptainer exec lean.sif".
RUN=${RUN:-}

JOB=${SLURM_JOB_ID:?run this under sbatch}
PKG_DIR=${PKG_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}
COMMIT=${COMMIT:-$(git -C "$PKG_DIR" rev-parse HEAD)}
OUT_DIR=${OUT_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}
LOG="$OUT_DIR/verify-$JOB.log"

# Node-local work directory. The receipt redacts /tmp/<user>-job<J> and /scratch/<user>/ by itself; a
# path elsewhere that appears in the log needs `--redact REGEX` at receipt time.
WORK=${SLURM_TMPDIR:-/tmp/${USER:-user}-job-$JOB}
SRC="$WORK/src"
mkdir -p "$SRC"
trap 'rm -rf "$SRC"' EXIT
# Stage the Lean runtime (the elan toolchain) and the Mathlib cache node-local before the build, e.g.
# copy the toolchain under "$WORK/elan" and export ELAN_HOME="$WORK/elan" PATH="$WORK/elan/bin:$PATH";
# a shared filesystem under many parallel readers is slow and can fail the build.
export MATHLIB_CACHE_DIR=${MATHLIB_CACHE_DIR:-$WORK/mathlib-cache}
# Mathlib-heavy packages can exhaust the node's file table (fs.file-max) at higher thread counts;
# /proc/sys/fs/file-nr goes into the log before and after the build.
export LEAN_NUM_THREADS=${LEAN_NUM_THREADS:-3}

# The committed tree at COMMIT, never the working tree.
git -C "$PKG_DIR" archive --format=tar "$COMMIT:./" | tar -xf - -C "$SRC"
cd "$SRC"
echo "pre-manifest-sha256 $(sha256sum lake-manifest.json | cut -d' ' -f1)"

{
  echo "commit: $COMMIT"
  echo "job-id: $JOB"
  echo "lean-toolchain: $(cat lean-toolchain)"
  echo "checker: $CHECKER"
  echo "lean-num-threads: $LEAN_NUM_THREADS"
  echo "fs-file-nr-before: $(cat /proc/sys/fs/file-nr 2>/dev/null || echo unavailable)"
} >"$LOG"
$RUN bash -c "$VERIFY_CMD" >>"$LOG" 2>&1
$RUN bash -c "$AXIOMS_CMD" >>"$LOG" 2>&1
echo "fs-file-nr-after: $(cat /proc/sys/fs/file-nr 2>/dev/null || echo unavailable)" >>"$LOG"

# The key lines, from Lean's own output in the log: sorry-count may not be lower than the
# `declaration uses `sorry`` warnings (either quote style), and axioms may not leave out a `#print axioms` result.
sorry=$(grep -c -E "declaration uses ['\`]sorry['\`]" "$LOG" || true)
# (newlines joined first: Lean wraps a long axiom list over several lines)
axioms=$(tr '\n' ' ' <"$LOG" | grep -o 'depends on axioms: \[[^]]*\]' | sed -e 's/^[^[]*\[//' -e 's/\]$//' \
  | tr ',' '\n' | tr -d ' ' | grep -v '^$' | sort -u | paste -sd, - || true)
{
  echo "sorry-count: $sorry"
  if [ -n "$axioms" ]; then
    echo "axioms: $axioms"
  elif grep -q 'does not depend on any axioms' "$LOG"; then
    echo "axioms: none"
  fi
  echo "generated-at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >>"$LOG"

# The seal: nothing may write to the log after this line.
echo "post-manifest-sha256 $(sha256sum lake-manifest.json | cut -d' ' -f1)"
echo "verify-log-sha256 $(sha256sum "$LOG" | cut -d' ' -f1)"
"""


# --------------------------------------------------------------------------
# job state

def sacct_text(job: str, sacct_file: str | None) -> tuple[str | None, str | None]:
    """(sacct output, refusal): the saved file, else `sacct` run on this host."""
    if sacct_file is not None:
        p = pathlib.Path(sacct_file)
        if not p.is_file():
            raise core.UsageError(f"--sacct-file {sacct_file}: no such file")
        return p.read_text(encoding="utf-8", errors="replace"), None
    exe = shutil.which("sacct")
    if exe is None:
        return None, ("sacct is not on PATH; on a host without Slurm save the output of "
                      f"`sacct -j {job} --format={SACCT_FORMAT} -P -n` on the cluster and pass --sacct-file")
    try:
        p = subprocess.run([exe, "-j", job, f"--format={SACCT_FORMAT}", "-P", "-n"],
                           capture_output=True, text=True, timeout=SACCT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"sacct could not run: {e}"
    if p.returncode != 0:
        return None, f"sacct exited {p.returncode}: {(p.stderr or p.stdout).strip() or 'no output'}"
    return p.stdout, None


# The states `sacct` reports (Slurm job_state_reason names aside); a second column that is none of them
# means the file was saved with another --format, so positions cannot be trusted.
SACCT_STATES = frozenset({
    "BOOT_FAIL", "CANCELLED", "COMPLETED", "COMPLETING", "CONFIGURING", "DEADLINE", "FAILED", "NODE_FAIL",
    "OUT_OF_MEMORY", "PENDING", "PREEMPTED", "REQUEUED", "REQUEUE_FED", "REQUEUE_HOLD", "RESIZING", "REVOKED",
    "RUNNING", "SIGNALING", "SPECIAL_EXIT", "STAGE_OUT", "STOPPED", "SUSPENDED", "TIMEOUT"})
_EXIT_RE = re.compile(r"\d+:\d+")


def check_job(job: str, text: str) -> tuple[dict | None, str | None]:
    """({job_id, state, exit_code, elapsed, total_cpu}, refusal) from `JobID|State|ExitCode[|Elapsed|TotalCPU]`.
    A header line (sacct without -n) naming JobID, State and ExitCode locates the columns by name, in any
    order. Without one the positions are checked: a second column that is not a Slurm state or a third that
    is not N:N is refused as an unexpected format, never read as an unclean finish."""
    want = {"jobid": "job_id", "state": "state", "exitcode": "exit_code", "elapsed": "elapsed",
            "totalcpu": "total_cpu"}
    index = {"job_id": 0, "state": 1, "exit_code": 2, "elapsed": 3, "total_cpu": 4}
    header = False
    expected = (f"`{SACCT_FORMAT}` with -P -n, or any --format with a header line naming JobID, State and "
                f"ExitCode (sacct -P without -n)")
    for raw in text.splitlines():
        cols = [c.strip() for c in raw.split("|")]
        names = [c.replace(" ", "").lower() for c in cols]
        if {"jobid", "state", "exitcode"} <= set(names):
            index = {want[n]: i for i, n in enumerate(names) if n in want}
            header = True
            continue
        if len(cols) < 3 or cols[index["job_id"]] != job:
            continue
        cols += [""] * (max(index.values()) + 1 - len(cols))
        seen = {k: (cols[i] or None) if k in ("elapsed", "total_cpu") else cols[i]
                for k, i in index.items()}
        for k in ("elapsed", "total_cpu"):
            seen.setdefault(k, None)
        state = seen["state"].split()[0] if seen["state"] else ""
        if not header and (state not in SACCT_STATES or not _EXIT_RE.fullmatch(seen["exit_code"])):
            return None, (f"sacct: unexpected sacct format: the job {job} line reads State={seen['state']!r}, "
                          f"ExitCode={seen['exit_code']!r}, which are not a Slurm state and an N:N exit code; "
                          f"expected {expected}")
        bad = []
        if state != "COMPLETED":
            bad.append(f"State={seen['state'] or '(empty)'}")
        if seen["exit_code"] != "0:0":
            bad.append(f"ExitCode={seen['exit_code'] or '(empty)'}")
        if bad:
            return seen, (f"sacct: job {job} is not a clean finish ({', '.join(bad)}); "
                          f"a receipt needs State=COMPLETED and ExitCode=0:0")
        return seen, None
    return None, (f"sacct: no main job line for job {job} (expected `{job}|COMPLETED|0:0|...` among the "
                  f"`{SACCT_FORMAT}` lines, or a header line naming JobID, State and ExitCode)")


# --------------------------------------------------------------------------
# seals

def check_self_hash(raw: bytes) -> str | None:
    """Fallback seal: exactly one `verify-log-sha256: <hex>` line in the log, equal to sha256(raw minus that line)."""
    lines = raw.split(b"\n")
    keep, found = [], []
    for i, line in enumerate(lines):
        m = _SHA_LINE.match(line)
        if m:
            found.append(m.group(1).decode().lower())
            continue
        keep.append(line + (b"\n" if i < len(lines) - 1 else b""))
    if not found:
        return (f"verify log: no `{SHA_KEY}: <64 hex>` line (pass the job's stdout with --stdout when the "
                f"seal is printed there)")
    if len(found) > 1:
        return f"verify log: {len(found)} `{SHA_KEY}` lines; exactly one is allowed"
    actual = hashlib.sha256(b"".join(keep)).hexdigest()
    if actual != found[0]:
        return (f"verify log: {SHA_KEY} line says {found[0][:12]}…, but the log without that line hashes "
                f"to {actual[:12]}…; the log was changed after it was sealed")
    return None


def stdout_values(text: str, key: str) -> list[str]:
    """The values of the job-stdout lines `KEY VALUE` / `KEY: VALUE` (key first on the line)."""
    rx = re.compile(r"^\s*" + re.escape(key) + r":?[ \t]+(\S+)")
    return [m.group(1) for m in map(rx.match, text.splitlines()) if m]


def _one_hex(text: str, key: str, refusals: list[str]) -> str | None:
    vals = stdout_values(text, key)
    if len(vals) != 1:
        refusals.append(f"job stdout: {len(vals)} `{key}` line(s); exactly one is required")
        return None
    if not _HEX64.fullmatch(vals[0]):
        refusals.append(f"job stdout: `{key}` is not 64 hex characters")
        return None
    return vals[0].lower()


def check_stdout(text: str, raw_sha: str, manifest: bytes | None, c12: str | None,
                 refusals: list[str], checks: list[str]) -> None:
    """The operator's seal: verify-log-sha256 in the job stdout = sha256(raw log); pre- and post-manifest
    hashes equal and equal to lake-manifest.json committed at the commit."""
    seal = _one_hex(text, SHA_KEY, refusals)
    if seal is not None:
        if seal != raw_sha:
            refusals.append(f"job stdout: {SHA_KEY} {seal[:12]}… is not the raw verify log's sha256 "
                            f"{raw_sha[:12]}…; the log is not the one the job sealed")
        else:
            checks.append(f"✓ seal: job stdout {SHA_KEY} = sha256(raw verify log) {raw_sha[:12]}…")
    pre, post = _one_hex(text, PRE_KEY, refusals), _one_hex(text, POST_KEY, refusals)
    if pre is None or post is None:
        return
    if pre != post:
        refusals.append(f"job stdout: {PRE_KEY} {pre[:12]}… != {POST_KEY} {post[:12]}…; the manifest "
                        f"changed during the job")
        return
    if c12 is None:
        return
    if manifest is None:
        refusals.append(f"no lake-manifest.json committed at {c12}; cannot check {PRE_KEY}")
        return
    man = hashlib.sha256(manifest).hexdigest()
    if pre != man:
        refusals.append(f"job stdout: manifest sha256 {pre[:12]}… is not lake-manifest.json committed at "
                        f"{c12} ({man[:12]}…)")
    else:
        checks.append(f"✓ manifest: pre = post = lake-manifest.json at {c12} ({man[:12]}…)")


# --------------------------------------------------------------------------
# the verify log

def redact(text: str, extra: list[re.Pattern], defaults=DEFAULT_REDACTIONS) -> tuple[str, int]:
    """(text with the executor's built-in redactions (default: the Slurm per-job prefixes) and every --redact
    match replaced, replacements made)."""
    n_total = 0
    for rx, repl in defaults:
        text, n = re.subn(rx, repl, text)
        n_total += n
    for rx in extra:
        text, n = rx.subn(REDACT_TOKEN, text)
        n_total += n
    return text, n_total


def lines_differing(a: str, b: str) -> int:
    la, lb = a.splitlines(), b.splitlines()
    return sum(1 for x, y in zip(la, lb) if x != y) + abs(len(la) - len(lb))


def parse_log(text: str) -> tuple[dict, list[str]]:
    """(key -> value, refusals) for the recognised `key: value` lines of the (redacted) log, plus the derived
    `sorry-count` and `axioms` when the log has no such line."""
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = _KEY_LINE.match(line.rstrip("\r"))
        if m and m.group(1) in LOG_KEYS:
            values.setdefault(m.group(1), []).append(m.group(2))
    out, refusals = {}, []
    for key in LOG_KEYS:
        got = values.get(key, [])
        if len(got) > 1:
            refusals.append(f"verify log: `{key}:` appears {len(got)} times; exactly one is allowed")
        elif got:
            out[key] = got[0]
    for key in ("lean-toolchain", "mathlib-rev", "checker"):
        if key in out and not out[key]:
            refusals.append(f"verify log: `{key}:` is empty")
            del out[key]
    if "sorry-count" in out:
        if not re.fullmatch(r"\d+", out["sorry-count"]):
            refusals.append(f"verify log: `sorry-count:` is not a non-negative integer: {out['sorry-count']!r}")
            out["sorry-count"] = None
        else:
            out["sorry-count"] = int(out["sorry-count"])
            # A declared count comes from the job's own script (often an inventory of declarations); Lean's
            # warnings in the same log are the ground truth, and the script may have missed a declaration
            # (a `lemma` outside a theorem-only inventory, field report). Never accept fewer than Lean printed.
            warned = len(_SORRY_RE.findall(text))
            if warned > out["sorry-count"]:
                refusals.append(f"verify log: `sorry-count: {out['sorry-count']}` but the log has {warned} "
                                f"`declaration uses 'sorry'` warning(s); a declared count may not hide Lean's own "
                                f"warnings (check what the script's inventory leaves out)")
    elif _SORRY_RE.search(text) or _BUILD_DONE_RE.search(text):
        out["sorry-count"] = len(_SORRY_RE.findall(text))
    else:
        # no count and no sign the build finished: a cut-short or wrong log would read as 0 (#82)
        refusals.append("verify log: no `sorry-count:` line and no build-completion line (`Build completed "
                        "successfully` or `lake build: ok`); the sorry count is unknown")
        out["sorry-count"] = None
    if out.get("sorry-count"):
        refusals.append(f"verify log: sorry count is {out['sorry-count']}; gate requires 0")
    printed = [[_no_universe(a) for a in parse_axioms(m)] for m in _DEPENDS_RE.findall(text)]
    if "axioms" in out:
        out["axioms"] = [_no_universe(a) for a in parse_axioms(out["axioms"])]
        # Like sorry-count: a declared list comes from the job's script; Lean's own `#print axioms` output in
        # the same log may not name an axiom the list leaves out.
        missing = sorted({a for lst in printed for a in lst} - set(out["axioms"]))
        if missing:
            refusals.append(f"verify log: `axioms:` omits {', '.join(missing)}, which the log's own `#print axioms` "
                            f"output reports; a declared list may not hide Lean's output (check what the script's "
                            f"inventory leaves out)")
    else:
        found = printed
        if not found and not _NO_AXIOMS_RE.search(text):
            refusals.append("verify log: no `axioms:` line and no `#print axioms` output "
                            "(`depends on axioms: [...]` / `does not depend on any axioms`)")
        else:
            union: list[str] = []
            for lst in found:
                union += [a for a in lst if a not in union]
            out["axioms"] = union
    if "generated-at" in out:
        try:
            core.parse_ts(out["generated-at"])
        except (ValueError, TypeError):
            refusals.append(f"verify log: `generated-at:` is not an ISO-8601 timestamp: {out['generated-at']!r}")
            del out["generated-at"]
    return out, refusals


# The log keys whose value the receipt takes from the raw log; `checker:` alone is published as redacted.
RAW_KEYS = ("lean-toolchain", "mathlib-rev", "sorry-count", "axioms", "commit", "job-id", "generated-at")


def _evidence_counts(text: str) -> tuple[int, int, int, int]:
    """(sorry warnings, `#print axioms` lists, no-axioms lines, build-completion lines) in a log text."""
    return (len(_SORRY_RE.findall(text)), len(_DEPENDS_RE.findall(text)), len(_NO_AXIOMS_RE.findall(text)),
            len(_BUILD_DONE_RE.findall(text)))


def _key_lines(text: str, key: str) -> list[str]:
    """The values of the log's `key:` lines, as written (a declared line removed by a redaction differs even
    when the derived value happens to agree)."""
    out = []
    for line in text.splitlines():
        m = _KEY_LINE.match(line.rstrip("\r"))
        if m and m.group(1) == key:
            out.append(m.group(2))
    return out


def parse_raw_log(raw_text: str, redacted: str) -> tuple[dict, list[str]]:
    """parse_log on the RAW log (review finding 3: a broad --redact must never delete the sorry or axiom
    evidence before it is counted), with `checker:` taken from the redacted log (the published text). A
    redaction that removed or altered a line the parser reads (Lean's sorry warnings, `#print axioms` output,
    a build-completion line, or any `key: value` line but `checker:`) refuses."""
    fields, problems = parse_log(raw_text)
    red_fields, _red_problems = parse_log(redacted)
    if "checker" in red_fields:
        fields["checker"] = red_fields["checker"]
    else:
        fields.pop("checker", None)
    changed = [k for k in RAW_KEYS if fields.get(k) != red_fields.get(k)
               or _key_lines(raw_text, k) != _key_lines(redacted, k)]
    names = ("`declaration uses sorry` warnings", "`#print axioms` results", "`does not depend on any axioms` lines",
             "build-completion lines")
    changed += [n for n, a, b in zip(names, _evidence_counts(raw_text), _evidence_counts(redacted)) if a != b]
    if changed:
        problems.append(f"redaction changed what the receipt is read from: {', '.join(changed)} differ between the "
                        f"raw and the redacted log; narrow the --redact pattern so it leaves those lines intact")
    return fields, problems


def _no_universe(name: str) -> str:
    """`Classical.choice.{u}` -> `Classical.choice`: Lean may print an axiom with its universe parameters; it is
    the same axiom, so lists are compared by the bare name."""
    return re.sub(r"\.\{[^{}]*\}$", "", name)


def parse_axioms(value: str) -> list[str]:
    """`propext, Quot.sound, Classical.choice` (brackets optional, commas and/or spaces); `none` or empty: []."""
    inner = value.strip().removeprefix("[").removesuffix("]").strip()
    if inner.casefold() in ("", "none"):
        return []
    out: list[str] = []
    for a in re.split(r"[,\s]+", inner):
        if a and a not in out:
            out.append(a)
    return out


def manifest_mathlib_rev(manifest: bytes | None) -> str | None:
    if manifest is None:
        return None
    try:
        data = json.loads(manifest.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    pkgs = data.get("packages") if isinstance(data, dict) else None
    for p in pkgs if isinstance(pkgs, list) else []:
        if isinstance(p, dict) and str(p.get("name", "")).casefold() == "mathlib" and p.get("rev"):
            return str(p["rev"])
    return None


# --------------------------------------------------------------------------
# the command

def resolve_commit(repo: pathlib.Path, sha: str) -> str | None:
    p = core.git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


# Local identity tokens shorter than this, or in this list, are never blocking tokens (too common in logs).
IDENTITY_MIN_LEN = 3
IDENTITY_STOPWORDS = frozenset({
    "admin", "build", "builder", "ci", "default", "docker", "false", "guest", "home", "host", "lean", "linux",
    "local", "localhost", "localdomain", "main", "nobody", "none", "null", "root", "runner", "server", "test",
    "true", "ubuntu", "user", "users", "worker"})
# A home directory with or without a trailing slash (the shared built-in family needs the slash).
HOME_DIR_RE = r"(?<![A-Za-z0-9_.-])/(?:home|Users)/[A-Za-z0-9._-]+(?![A-Za-z0-9._-])"


def local_identity() -> dict[str, str]:
    """{token: label}: this machine's user name (getpass.getuser(), $USER, $LOGNAME) and host name
    (socket.gethostname(), full and its first label), derived when the receipt is written, never stored.
    Labels name the source, never the value."""
    found: dict[str, str] = {}
    users = []
    try:
        users.append(getpass.getuser())
    except Exception:          # no passwd entry and no environment variable: nothing to derive
        pass
    users += [os.environ.get("USER") or "", os.environ.get("LOGNAME") or ""]
    try:
        host = socket.gethostname() or ""
    except OSError:
        host = ""
    for label, values in (("local user name", users), ("local host name", [host, host.split(".")[0]])):
        for v in values:
            v = (v or "").strip()
            if (len(v) >= IDENTITY_MIN_LEN and v.casefold() not in IDENTITY_STOPWORDS
                    and re.search(r"[A-Za-z]", v)):
                found.setdefault(v, label)
    return found


def _scanner(repo: pathlib.Path) -> ps.Scanner:
    sc = ps.Scanner({}, repo, None)
    sc.emails = set()                  # a receipt declares no address: every one is a hit
    extra = [ps.Pattern("home-path", "builtin", re.compile(HOME_DIR_RE))]   # same family as the shared one
    for tok, label in local_identity().items():
        rx = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(tok) + r"(?![A-Za-z0-9_])", re.I)
        extra.append(ps.Pattern(label, "derived", rx))
    sc.patterns = sc.patterns + extra
    sc._active = {}
    for problem in sc.problems:
        print(f"lean-pkg: WARN {problem}", file=sys.stderr)
    return sc


def blocking_families(sc: ps.Scanner, text: str) -> list[str]:
    return sorted({ps.PATTERNS_FILE if pat.kind == "file" else pat.label
                   for _n, pat, _ex in sc.scan_text(text) if pat.severity == ps.BLOCK})


def _shown(path: pathlib.Path, repo: pathlib.Path) -> str:
    """The path relative to the repository when inside it, else its file name (never an absolute path,
    which would carry a user name into the README)."""
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.name


def _same_or_absent(path: pathlib.Path, data: bytes) -> str | None:
    if path.is_symlink():
        return f"{path.name}: a symlink is in the way; nothing written"
    if path.exists() and path.read_bytes() != data:
        return f"{path.name} already exists with different content; remove it first (nothing written)"
    return None


def _kept_fallback_time(path: pathlib.Path, receipt: dict) -> str | None:
    """#85: the `generated_at` of the receipt already at `path` when it is identical to `receipt` but for that
    field (same keys, same order, same values) and that field is a valid timestamp, else None. Only used when the
    new `generated_at` came from the clock, so an identical re-run in a later second is not refused."""
    if path.is_symlink() or not path.is_file():
        return None
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(old, dict) or list(old) != list(receipt) or not isinstance(old.get("generated_at"), str):
        return None
    try:
        core.parse_ts(old["generated_at"])
    except (ValueError, TypeError):
        return None
    if any(old[k] != v for k, v in receipt.items() if k != "generated_at"):
        return None
    return old["generated_at"]


def _write(path: pathlib.Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def readme_block(*, commit: str, heading: str, executor_lines: list[str], receipt: dict, log_name: str,
                 raw_sha: str, n_diff: int, redaction_note: str, extra: list[str], seal: dict | None = None,
                 evidence_lines: list[str] | None = None) -> str:
    """The `.lean-receipts/README.md` entry, in the field operator's block shape: the executor's own lines
    (job or unit state), then checker, log, any further evidence lines, and the seal. `seal`
    ({job_stdout_sha256, manifest_sha256}, only with --stdout) adds the line naming what the seal was checked
    against."""
    axioms = ", ".join(receipt["axioms"]) or "none"
    # The heading gets the commit prefix itself; a --readme-heading that already starts with this commit
    # (`4095a30`: ... or 4095a30: ...) would print it twice (field report). Any other SHA is kept.
    m = re.match(r"`?([0-9a-f]{7,40})`?:\s*", heading)
    if m and commit.startswith(m.group(1)):
        heading = heading[m.end():]
    lines = [f"## `{commit[:7]}`: {heading}", "", *executor_lines,
             f"- Checker: {receipt['checker']}; sorry_count 0; axioms {axioms}.",
             f"- Log: `{log_name}`, SHA-256 `{receipt['report_sha256']}` (= `report_sha256`);",
             f"  raw log SHA-256 `{raw_sha}` ({n_diff} line(s) differ: {redaction_note})."]
    lines += evidence_lines or []
    if seal:
        lines.append(f"- Seal: job stdout SHA-256 `{seal['job_stdout_sha256']}` carries {SHA_KEY} = raw log "
                     f"SHA-256 `{raw_sha}`; lake-manifest.json SHA-256 `{seal['manifest_sha256']}` (pre = post).")
    return "\n".join(lines + [x for x in extra if x.strip()]) + "\n"


def common_usage(args) -> list[re.Pattern]:
    """The usage checks every `receipt` form shares (exit 2); returns the compiled --redact patterns."""
    if args.commit_sha is not None and (not args.commit_sha.strip() or args.commit_sha.startswith("-")):
        raise core.UsageError(f"--commit must name a commit, got {args.commit_sha!r}")
    if args.tar_sha256 is not None and not _HEX64.fullmatch(args.tar_sha256):
        raise core.UsageError("--tar-sha256 must be 64 hex characters")
    if args.checker is not None and not args.checker.strip():
        raise core.UsageError("--checker must not be empty")
    extra = []
    for r in args.redact or []:
        try:
            extra.append(re.compile(r))
        except re.error as e:
            raise core.UsageError(f"--redact {r!r} is not a valid regular expression ({e.msg})") from None
    return extra


def repo_dir(args) -> pathlib.Path:
    repo = pathlib.Path(args.repo or ".").expanduser()
    if not repo.is_dir():
        raise core.UsageError(f"--repo {repo}: not a directory")
    return repo


class Evidence:
    """What an executor (`receipt slurm`, `receipt systemd`) hands the shared pipeline in `produce`: its job
    identity, the refusals and check lines of its own terminal-state checks (listed first), its README lines,
    and the extra evidence files and receipt keys it persists."""

    def __init__(self, *, verb: str, job_id: str, refusals: list[str], checks: list[str],
                 executor_lines, default_heading: str, check_log_job_id, json_extra: dict,
                 redactions=DEFAULT_REDACTIONS, redaction_note: str = "per-job paths redacted",
                 stdout_check=None, evidence_files=(), redact_help: str = "add a --redact REGEX for it"):
        self.verb, self.job_id = verb, job_id
        self.refusals, self.checks = refusals, checks
        self.executor_lines = executor_lines          # (receipt file name, tar clause) -> list[str]
        self.default_heading = default_heading
        self.check_log_job_id = check_log_job_id      # the log's `job-id:` value -> refusal or None
        self.json_extra = json_extra
        self.redactions, self.redaction_note = redactions, redaction_note
        self.stdout_check = stdout_check              # (job stdout text, refusals, checks) -> None
        # (suffix, bytes, receipt key, README line template with {name} and {sha}): written beside the receipt
        # as `<c12>-<slug>-<suffix>`, its SHA-256 stored under the receipt key
        self.evidence_files = list(evidence_files)
        self.redact_help = redact_help


def check_seal(paths: dict, raw: bytes, manifest: bytes | None, c12: str | None, refusals: list[str],
               checks: list[str], stdout_check=None) -> dict | None:
    """The seal (stdout, else the in-log fallback) and the empty stderr; returns the seal evidence persisted
    with --stdout (#72), else None."""
    raw_sha = hashlib.sha256(raw).hexdigest()
    seal = None
    if "--stdout" in paths:
        out_raw = paths["--stdout"].read_bytes()
        out_text = out_raw.decode("utf-8", errors="replace")
        check_stdout(out_text, raw_sha, manifest, c12, refusals, checks)
        if stdout_check:
            stdout_check(out_text, refusals, checks)
        seal = {"job_stdout_sha256": hashlib.sha256(out_raw).hexdigest(),
                "manifest_sha256": hashlib.sha256(manifest).hexdigest() if manifest is not None else None}
    else:
        why = check_self_hash(raw)
        if why:
            refusals.append(why)
        else:
            checks.append(f"✓ seal: in-log {SHA_KEY} (fallback; no --stdout)")
        checks.append("· manifest: not checked (no --stdout)")
    if "--stderr" in paths:
        n = paths["--stderr"].stat().st_size
        if n:
            refusals.append(f"job stderr is not empty ({n} bytes); a clean job writes nothing there")
        else:
            checks.append("✓ stderr: empty")
    else:
        checks.append("· stderr: not checked (no --stderr)")
    return seal


def check_fields(fields: dict, *, commit: str | None, toolchain: str | None, manifest: bytes | None,
                 checker_flag: str | None, check_log_job_id, refusals: list[str]) -> tuple[str | None, str | None]:
    """(mathlib_rev, checker): the log's fields against the commit and the flags. Without a commit (preflight
    without --commit) the commit-side comparisons are skipped."""
    c12 = commit[:12] if commit else None
    if toolchain is not None and "lean-toolchain" in fields and fields["lean-toolchain"] != toolchain:
        refusals.append(f"toolchain mismatch: verify log says {fields['lean-toolchain']!r}, "
                        f"lean-toolchain committed at {c12} says {toolchain!r}")
    mathlib = manifest_mathlib_rev(manifest)
    if mathlib and "mathlib-rev" in fields and fields["mathlib-rev"] != mathlib:
        refusals.append(f"mathlib mismatch: verify log says {fields['mathlib-rev']!r}, lake-manifest.json "
                        f"committed at {c12} says {mathlib!r}")
    mathlib = mathlib or fields.get("mathlib-rev")
    if commit is not None and not mathlib:
        refusals.append(f"no mathlib rev: no `mathlib` package in lake-manifest.json at {c12} and no "
                        f"`mathlib-rev:` line in the verify log")
    checker = checker_flag.strip() if checker_flag else fields.get("checker")
    if not checker:
        refusals.append("no checker: pass --checker TEXT (the exact checker command or mode) or print a "
                        "`checker:` line in the verify log")
    if commit is not None and "commit" in fields:
        claimed = fields["commit"].lower()
        if len(claimed) < 7 or not commit.startswith(claimed):
            refusals.append(f"verify log: `commit:` {fields['commit']!r} is not --commit {c12}")
    if "job-id" in fields and check_log_job_id is not None:
        why = check_log_job_id(fields["job-id"])
        if why:
            refusals.append(why)
    return mathlib, checker


NO_COMMIT_LINE = ("the verify log has no commit: line; it is bound to the commit only through "
                  "lake-manifest.json")


def commit_line_binding(fields: dict, commit: str | None, stdout_seal: bool, refusals: list[str],
                        checks: list[str]) -> bool | None:
    """The verify log's own binding to --commit (review finding 5): a `commit:` line must equal --commit
    (check_fields refuses a mismatch). Without one, the fallback seal (no --stdout: only the log's own hash)
    binds the log to nothing, so it refuses; with --stdout the manifest seal is the only binding: `produce`
    prints a stderr WARN and `log_commit_bound: false`. Returns True / False, None without a resolved commit."""
    if commit is None:
        return None
    if "commit" in fields:
        claimed = fields["commit"].lower()
        if len(claimed) >= 7 and commit.startswith(claimed):
            checks.append(f"✓ log: `commit:` line = --commit {commit[:12]}")
            return True
        return False                       # check_fields has refused the mismatch
    if not stdout_seal:
        refusals.append(f"verify log: no `commit:` line; with the in-log fallback seal (no --stdout) nothing "
                        f"binds the log to --commit {commit[:12]} (print `commit: <sha>` in the log, as the "
                        f"--template does, or pass the job's stdout with --stdout)")
        return False
    checks.append(f"· log: no `commit:` line; {NO_COMMIT_LINE.split('; ', 1)[1]}")
    return False


def evidence_symlink_problem(repo: pathlib.Path, commit: str | None) -> str | None:
    """Review finding 1: a committed symlink at --commit that resolves into .lean-receipts/ (gate refuses it:
    a later "evidence" edit would change what the build reads)."""
    if commit is None:
        return None
    try:
        links = core.evidence_symlinks(core.bound_tree(repo, commit))
    except core.Fail as e:
        return str(e)
    if links:
        return (f"symlinks at {commit[:12]} point into .lean-receipts/: {', '.join(links)}; gate refuses such a "
                f"commit (the evidence-only rule would let the target change); replace them with the files")
    return None


def produce(args, ev: Evidence, repo: pathlib.Path, paths: dict, extra: list[re.Pattern]) -> int:
    """The shared second half of every `receipt` form: commit facts, the seal, the verify log's fields, redaction,
    the public-safety scan, and (when nothing refused) the receipt, the redacted log, the executor's evidence
    files and the README entry. Every check runs before anything is written."""
    refusals, checks = ev.refusals, ev.checks
    slug = core.receipt_job_slug(ev.job_id)
    commit = resolve_commit(repo, args.commit_sha)
    c12 = commit[:12] if commit else None
    toolchain = manifest = None
    if commit is None:
        refusals.append(f"--commit {args.commit_sha}: not a commit in the --repo checkout "
                        f"(default the current directory)")
    else:
        blob = core.git_show_bytes(repo, "./lean-toolchain", commit)
        if blob is None:
            refusals.append(f"no lean-toolchain committed at {c12}")
        else:
            toolchain = blob.decode("utf-8", "replace").strip()
        manifest = core.git_show_bytes(repo, "./lake-manifest.json", commit)

    raw = paths["--log"].read_bytes()
    raw_sha = hashlib.sha256(raw).hexdigest()
    seal = check_seal(paths, raw, manifest, c12, refusals, checks, ev.stdout_check)

    raw_text = raw.decode("utf-8", errors="replace")
    redacted, n_redacted = redact(raw_text, extra, ev.redactions)
    n_diff = lines_differing(raw_text, redacted)
    fields, problems = parse_raw_log(raw_text, redacted)
    refusals += problems
    mathlib, checker = check_fields(fields, commit=commit, toolchain=toolchain, manifest=manifest,
                                    checker_flag=args.checker, check_log_job_id=ev.check_log_job_id,
                                    refusals=refusals)
    log_commit_bound = commit_line_binding(fields, commit, "--stdout" in paths, refusals, checks)
    why = evidence_symlink_problem(repo, commit)
    if why:
        refusals.append(why)

    name12 = c12 or "0" * 12
    out = (pathlib.Path(args.out).expanduser() if args.out
           else repo / ".lean-receipts" / f"{name12}-{slug}.json")
    log_out = out.with_name(f"{name12}-{slug}-verify.redacted.log")
    log_bytes = redacted.encode("utf-8")
    report_sha = hashlib.sha256(log_bytes).hexdigest()
    receipt = {
        "commit": commit,
        "lean_toolchain": toolchain,
        "mathlib_rev": mathlib,
        "sorry_count": fields.get("sorry-count"),
        "axioms": fields.get("axioms", []),
        "report_sha256": report_sha,
        "job_id": ev.job_id,
        "checker": checker,
        "generated_at": fields.get("generated-at") or core.now_iso(),
    }
    if seal:
        receipt.update(seal)       # additive keys: validate_receipt and gate ignore keys beyond RECEIPT_FIELDS
    files = []                     # (path, bytes) of the executor's evidence files
    sealed_files = []              # {role, path, sha256, receipt_key} of each evidence file, for --json
    evidence_lines = []
    for suffix, data, key, line in ev.evidence_files:
        path = out.with_name(f"{name12}-{slug}-{suffix}")
        digest = hashlib.sha256(data).hexdigest()
        receipt[key] = digest      # additive, like the seal keys
        files.append((path, data))
        sealed_files.append({"role": suffix, "path": str(path), "sha256": digest, "receipt_key": key})
        evidence_lines.append(line.format(name=path.name, sha=digest))
    if not fields.get("generated-at"):
        kept = _kept_fallback_time(out, receipt)
        if kept and kept != receipt["generated_at"]:
            receipt["generated_at"] = kept
            checks.append(f"· {out.name} already present, identical but for its clock time; kept "
                          f"generated_at {kept}")
    receipt_bytes = (json.dumps(receipt, indent=2) + "\n").encode("utf-8")
    rel_r, rel_l = _shown(out, repo), _shown(log_out, repo)
    tar = f"; tar SHA-256 `{args.tar_sha256}`" if args.tar_sha256 else ""
    block = readme_block(commit=commit or name12, heading=args.readme_heading or ev.default_heading,
                         executor_lines=ev.executor_lines(out.name, tar), receipt=receipt, log_name=log_out.name,
                         raw_sha=raw_sha, n_diff=n_diff, redaction_note=ev.redaction_note,
                         extra=args.readme_line or [], seal=seal, evidence_lines=evidence_lines)

    sc = _scanner(repo)
    scanned = [("redacted verify log", redacted)]
    scanned += [(path.name, data.decode("utf-8", "replace")) for path, data in files]
    scanned += [("receipt JSON", receipt_bytes.decode()), ("README block", block)]
    for what, blob_text in scanned:
        fams = blocking_families(sc, blob_text)
        if fams:
            refusals.append(f"public-safety: a private string remains in the {what} ({', '.join(fams)}); "
                            + (ev.redact_help if what != "README block"
                               else "fix --readme-heading / --readme-line"))
    if not refusals:
        for path, data in ((log_out, log_bytes), *files, (out, receipt_bytes)):
            why = _same_or_absent(path, data)
            if why:
                refusals.append(why)
    readme = out.parent / "README.md"
    if not refusals and out.parent.is_symlink():
        refusals.append(f"{out.parent.name}/ is a symlink; nothing written")
    readme_old = None
    if not refusals and args.append_readme:
        if readme.is_symlink():
            refusals.append("README.md beside the receipt is a symlink; nothing written")
        elif readme.exists():
            try:
                readme_old = readme.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError) as e:
                refusals.append(f"README.md beside the receipt cannot be read as UTF-8 text ({e.__class__.__name__}"
                                f"); fix or move it, or drop --append-readme; nothing written")

    appended = False
    next_cmd = None
    if not refusals:
        checks.append(f"✓ public-safety: no blocking hit after {n_redacted} redaction(s)")
        out.parent.mkdir(parents=True, exist_ok=True)
        _write(log_out, log_bytes)
        for path, data in files:
            _write(path, data)
        _write(out, receipt_bytes)
        if args.append_readme:
            old = readme_old if readme_old is not None else "# Receipts\n"
            if f"`{out.name}`" in old:
                checks.append(f"· README.md already lists {out.name}; not appended again")
            else:
                _write(readme, (old.rstrip() + "\n\n" + block).encode("utf-8"))
                appended = True
        next_cmd = f"lean-pkg gate NAME --check --receipt {rel_r}"

    if args.json_out:
        payload = {"ok": not refusals, "receipt": None if refusals else str(out),
                   "log": None if refusals else str(log_out),
                   "report_sha256": None if refusals else report_sha, "raw_log_sha256": raw_sha,
                   "job_id": ev.job_id, "seal": seal, "log_commit_bound": log_commit_bound}
        payload.update(ev.json_extra)
        # The exact files this run wrote and bound, so a caller never guesses a sidecar by its name: the
        # receipt itself, the redacted log (report_sha256) and each evidence file with the receipt key that
        # holds its SHA-256. Empty on a refusal (nothing written).
        payload["sealed"] = [] if refusals else (
            [{"role": "receipt", "path": str(out), "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
              "receipt_key": None},
             {"role": "redacted-log", "path": str(log_out), "sha256": report_sha, "receipt_key": "report_sha256"}]
            + sealed_files)
        payload.update({"redactions": n_redacted, "lines_redacted": n_diff, "checks": checks,
                        "readme_block": None if refusals else block, "readme_appended": appended,
                        "next": next_cmd, "refusals": refusals})
        core.print_json(payload)
    if log_commit_bound is False and not refusals:
        print(f"lean-pkg: WARN {NO_COMMIT_LINE}", file=sys.stderr)
    if refusals:
        verdict = f"{ev.verb}: refused, {len(refusals)} problem(s); nothing written"
        print(f"lean-pkg: {verdict}", file=sys.stderr)
        for r in refusals:
            print(f"  ✗ {r}", file=sys.stderr)
        print(f"lean-pkg: {verdict}", file=sys.stderr)
        return 1
    if not args.json_out:
        print(f"{ev.verb}: ok, job {ev.job_id} at commit {c12}; {n_redacted} redaction(s)")
        for c in checks:
            print(f"  {c}")
        print(f"  receipt     {rel_r}")
        print(f"  verify log  {rel_l} (redacted; report_sha256 {report_sha})")
        for path, _data in files:
            print(f"  evidence    {_shown(path, repo)}")
        if appended:
            print(f"README entry appended to {_shown(readme, repo)}:")
        else:
            print("README entry to add (or rerun with --append-readme):")
        print(block.rstrip("\n"))
        print(f"next: {next_cmd}")
    allowed = set(core.DEFAULT_AXIOMS)
    if set(receipt["axioms"]) - allowed:
        print(f"lean-pkg: WARN axioms outside the default allowed set: "
              f"{sorted(set(receipt['axioms']) - allowed)}; gate refuses them unless the package's "
              f"gate.axioms_allowed lists them", file=sys.stderr)
    return 0


def _job_files(args) -> dict:
    paths = {}
    for flag, value in (("--log", args.log), ("--stdout", args.stdout), ("--stderr", args.stderr)):
        if value is not None:
            p = pathlib.Path(value).expanduser()
            if not p.is_file():
                raise core.UsageError(f"{flag} {value}: no such file")
            paths[flag] = p
    return paths


def preflight(args) -> int:
    """`receipt slurm --check` (field report): every check on the job's files that needs neither sacct nor a
    finished long job, on the .log/.out/.err of a trial run; every problem is listed, nothing is written. The
    commit-side checks run only with --commit, the sacct check only with --sacct-file and --job."""
    if args.log is None:
        raise core.UsageError("--check needs --log PATH (the job's raw verify log)")
    job = (args.job or "").strip().removeprefix("slurm-") or None
    repo = repo_dir(args)
    paths = _job_files(args)
    extra = common_usage(args)
    problems: list[str] = []
    checks: list[str] = []
    if args.sacct_file is not None and job:
        text, _why = sacct_text(job, args.sacct_file)
        _s, why = check_job(job, text)
        if why:
            problems.append(why)
        else:
            checks.append(f"✓ sacct: job {job} COMPLETED, exit 0:0 (--sacct-file)")
    else:
        checks.append("· sacct: not checked in preflight (the receipt needs the main job line COMPLETED, exit "
                      "0:0; give --sacct-file F --job J to check a saved copy)")
    commit = toolchain = manifest = None
    if args.commit_sha is None:
        checks.append("· commit: not checked (no --commit): the manifest hashes against lake-manifest.json, "
                      "and lean-toolchain:, mathlib-rev:, commit: against the commit")
    else:
        commit = resolve_commit(repo, args.commit_sha)
        if commit is None:
            problems.append(f"--commit {args.commit_sha}: not a commit in the --repo checkout "
                            f"(default the current directory)")
        else:
            blob = core.git_show_bytes(repo, "./lean-toolchain", commit)
            if blob is None:
                problems.append(f"no lean-toolchain committed at {commit[:12]}")
            else:
                toolchain = blob.decode("utf-8", "replace").strip()
            manifest = core.git_show_bytes(repo, "./lake-manifest.json", commit)
    raw = paths["--log"].read_bytes()
    check_seal(paths, raw, manifest, commit[:12] if commit else None, problems, checks)
    raw_text = raw.decode("utf-8", errors="replace")
    redacted, _n = redact(raw_text, extra)
    fields, found = parse_raw_log(raw_text, redacted)
    problems += found

    def log_job_id(value: str) -> str | None:
        if job and value.removeprefix("slurm-") != job:
            return f"verify log: `job-id:` {value!r} is not --job {job!r}"
        return None

    check_fields(fields, commit=commit, toolchain=toolchain, manifest=manifest, checker_flag=args.checker,
                 check_log_job_id=log_job_id, refusals=problems)
    if commit is not None:
        commit_line_binding(fields, commit, "--stdout" in paths, problems, checks)
        why = evidence_symlink_problem(repo, commit)
        if why:
            problems.append(why)
    elif "commit" not in fields and "--stdout" not in paths:
        problems.append("verify log: no `commit:` line; with the in-log fallback seal (no --stdout) the receipt "
                        "is refused (print `commit: <sha>` in the log, or pass --stdout)")
    fams = blocking_families(_scanner(repo), redacted)
    if fams:
        problems.append(f"public-safety: a private string remains in the redacted verify log ({', '.join(fams)}); "
                        f"add a --redact REGEX for it")
    ok = not problems
    if args.json_out:
        core.print_json({"ok": ok, "check": True, "written": [], "raw_log_sha256": hashlib.sha256(raw).hexdigest(),
                         "checks": checks, "problems": problems})
    else:
        print(f"receipt slurm --check: {'ok' if ok else f'{len(problems)} problem(s)'}; nothing written")
        for c in checks:
            print(f"  {c}")
        for pr in problems:
            print(f"  ✗ {pr}")
    return 0 if ok else 1


def cmd_receipt_slurm(args) -> int:
    if args.template:
        if args.check:
            raise core.UsageError("--template and --check do not combine")
        sys.stdout.write(SBATCH_TEMPLATE)
        return 0
    if args.check:
        return preflight(args)
    missing = [f for f, v in (("--job", args.job), ("--log", args.log), ("--commit", args.commit_sha)) if v is None]
    if missing:
        raise core.UsageError(f"receipt slurm needs {', '.join(missing)} (or --template, or --check on a job's files)")
    job = (args.job or "").strip().removeprefix("slurm-")
    if not job:
        raise core.UsageError("--job must not be empty")
    job_id = f"slurm-{job}"
    core.receipt_job_slug(job_id)
    repo = repo_dir(args)
    paths = _job_files(args)
    extra = common_usage(args)

    refusals: list[str] = []
    checks: list[str] = []
    text, why = sacct_text(job, args.sacct_file)
    sacct = None
    if why:
        refusals.append(why)
    else:
        sacct, why = check_job(job, text)
        if why:
            refusals.append(why)
        else:
            checks.append(f"✓ sacct: job {job} COMPLETED, exit 0:0")
    s = sacct or {}

    def lines(receipt_name: str, tar: str) -> list[str]:
        return [f"- Receipt: `{receipt_name}`. Slurm job `{job}`: COMPLETED, exit `0:0`,",
                f"  elapsed {s.get('elapsed') or 'unknown'}, TotalCPU {s.get('total_cpu') or 'unknown'}{tar}."]

    def log_job_id(value: str) -> str | None:
        if value.removeprefix("slurm-") != job:
            return f"verify log: `job-id:` {value!r} is not --job {job!r}"
        return None

    ev = Evidence(verb="receipt slurm", job_id=job_id, refusals=refusals, checks=checks, executor_lines=lines,
                  default_heading=f"lower-tier receipt, Slurm job {job}", check_log_job_id=log_job_id,
                  json_extra={"sacct": sacct})
    return produce(args, ev, repo, paths, extra)


def add_common_flags(s, kind: str, name_hint: str) -> None:
    """The flags every `receipt` form shares, after its executor's own."""
    s.add_argument("--checker", default=None, metavar="TEXT",
                   help="the exact checker command or mode (default: the log's `checker:` line)")
    s.add_argument("--repo", default=None, metavar="DIR", help="the package checkout (default: the current directory)")
    s.add_argument("--out", default=None, metavar="PATH",
                   help=f"receipt path (default <repo>/.lean-receipts/<commit12>-{name_hint}.json); the redacted log "
                        f"is written beside it as <commit12>-{name_hint}-verify.redacted.log")
    s.add_argument("--append-readme", action="store_true",
                   help="append the entry block to README.md beside the receipt (created if absent; an entry "
                        "already naming the receipt is not repeated)")
    s.add_argument("--readme-heading", default=None, metavar="TEXT",
                   help=f"the entry heading after the short commit (default: lower-tier receipt, {kind})")
    s.add_argument("--readme-line", action="append", default=[], metavar="TEXT",
                   help="an extra line at the end of the entry block (repeatable)")
    s.add_argument("--tar-sha256", default=None, metavar="HEX",
                   help="the frozen source tarball's SHA-256, named in the entry block")
    s.add_argument("--json", dest="json_out", action="store_true", default=argparse.SUPPRESS,
                   help="machine-readable JSON on stdout")


def add_parsers(sub, add_json) -> None:
    del add_json   # its --commit flag (committed mode) would collide with `receipt slurm --commit SHA`
    from . import receipt_systemd
    r = sub.add_parser("receipt", help="produce lower-tier receipts (writes files in the package checkout, "
                                       "never the registry)")
    rsub = r.add_subparsers(dest="receipt_cmd", metavar="{slurm,systemd}", required=True)
    s = rsub.add_parser("slurm", help="a lower-tier receipt from a finished Slurm job, its verify log and "
                                      "its stdout seal (--template: an sbatch wrapper that meets it; --check: "
                                      "preflight the job's files)")
    s.add_argument("--job", default=None, metavar="J",
                   help="the Slurm job id (job_id becomes slurm-J); required unless --template or --check")
    s.add_argument("--log", default=None, metavar="PATH",
                   help="the job's raw verify log (required unless --template); when it has no `checker:` line, "
                        "--checker TEXT is required")
    s.add_argument("--commit", dest="commit_sha", default=None, metavar="SHA",
                   help="the verified commit; must exist in --repo; required unless --template or --check")
    s.add_argument("--template", action="store_true",
                   help="print a minimal sbatch wrapper whose .out/.err/log meet every check below (placeholders "
                        "for the package's own commands); needs no other flag")
    s.add_argument("--check", action="store_true",
                   help="preflight the files of a finished (trial) job: every seal, stderr and log-line check, "
                        "each problem named; no sacct (unless --sacct-file), commit checks only with --commit; "
                        "writes nothing, exit 0/1")
    s.add_argument("--stdout", default=None, metavar="PATH",
                   help=f"the job's stdout (.out): {SHA_KEY} = sha256(raw log), {PRE_KEY} = {POST_KEY} = "
                        "sha256(lake-manifest.json at the commit); without it the log must carry its own seal")
    s.add_argument("--stderr", default=None, metavar="PATH", help="the job's stderr (.err); must be empty")
    s.add_argument("--redact", action="append", default=[], metavar="REGEX",
                   help=f"also replace matches with {REDACT_TOKEN} (repeatable); the built-in prefixes are always "
                        f"redacted to {USER_TOKEN} instead (/tmp/{USER_TOKEN}-job, /scratch/{USER_TOKEN}/)")
    s.add_argument("--sacct-file", default=None, metavar="F",
                   help=f"saved output of `sacct -j J --format={SACCT_FORMAT} -P -n` (a host without Slurm)")
    add_common_flags(s, "Slurm job J", "slurm-<J>")
    s.set_defaults(fn=cmd_receipt_slurm)
    receipt_systemd.add_parser(rsub)
