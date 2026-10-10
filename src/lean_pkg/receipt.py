# lean-pkg receipt slurm -- a lower-tier receipt from a finished Slurm job
"""`lean-pkg receipt slurm --job J --log PATH --commit SHA [--repo DIR] [--redact REGEX ...]
[--sacct-file F] [--out PATH] [--json]` (#70): turn a Slurm verification job and its verify log into a
lower-tier receipt in exactly the shape `gate --receipt` validates. It never reads or writes a registry.

Checks, all run before anything is written (every failed check is listed; any one refuses, exit 1):

1. Job state: `sacct -j J --format=JobID,State,ExitCode -P -n` (or the same output saved to
   `--sacct-file`, for a host without Slurm). The main job line (JobID equal to J, not a `.batch` or
   `.extern` step) must say State `COMPLETED` and ExitCode `0:0`.
2. Log self-hash: the raw log must hold exactly one line `verify-log-sha256: <64 hex>`, and that value
   must equal the SHA-256 of the raw log bytes with that one line (and its line ending) removed. A script
   that hashes the log and then appends the line satisfies it.
3. Fields: the log lines `lean-toolchain:`, `mathlib-rev:`, `checker:`, `sorry-count:` and `axioms:`
   (key at the start of the line, each exactly once; optional `commit:`, `job-id:`, `generated-at:`).
   `sorry-count` must be 0. `--commit` must resolve in `--repo` and its committed `lean-toolchain` must
   equal the log's; a `commit:` or `job-id:` line, when present, must agree with `--commit` / `--job`.
4. Redaction: the per-job temporary prefixes (DEFAULT_REDACTIONS) and every `--redact REGEX` are
   replaced in the log text; then the public-safety scanner (built-in families plus the repository's
   `.lean-pkg-private-patterns`, when present) runs over the redacted log and the receipt JSON. Any
   blocking hit refuses, naming the families only.

Output: the redacted log `<c12>-<job>.verify.log` and the receipt `<c12>-<job>.json` (default directory
`<repo>/.lean-receipts/`; `--out PATH` names the receipt, the log goes beside it). `report_sha256` is the
SHA-256 of the redacted log file as written. A differing file already at either path is refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

from . import cli as core
from . import public_safety as ps

SHA_KEY = "verify-log-sha256"
REQUIRED_KEYS = ("lean-toolchain", "mathlib-rev", "checker", "sorry-count", "axioms")
OPTIONAL_KEYS = ("commit", "job-id", "generated-at")
# (regex, replacement): the per-job temporary account/directory prefixes a Slurm verify script prints.
DEFAULT_REDACTIONS = (
    (r"/tmp/[^/\s]+-job\d+/", "/tmp/<job-tmp>/"),
    (r"/scratch/[^/\s]+/", "/scratch/<user>/"),
)
REDACT_TOKEN = "<redacted>"
SACCT_FORMAT = "JobID,State,ExitCode"
SACCT_TIMEOUT = 60
_KEY_LINE = re.compile(r"^([a-z0-9-]+):[ \t]*(.*?)[ \t]*$")
_SHA_LINE = re.compile(rb"^" + SHA_KEY.encode() + rb":[ \t]*([0-9A-Fa-f]{64})[ \t]*\r?$")


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


def check_job(job: str, text: str) -> tuple[dict | None, str | None]:
    """({job_id, state, exit_code}, refusal) from `JobID|State|ExitCode` lines."""
    for raw in text.splitlines():
        cols = [c.strip() for c in raw.split("|")]
        if len(cols) < 3 or cols[0] != job:
            continue
        state = cols[1].split()[0] if cols[1] else ""
        seen = {"job_id": cols[0], "state": cols[1], "exit_code": cols[2]}
        bad = []
        if state != "COMPLETED":
            bad.append(f"State={cols[1] or '(empty)'}")
        if cols[2] != "0:0":
            bad.append(f"ExitCode={cols[2] or '(empty)'}")
        if bad:
            return seen, (f"sacct: job {job} is not a clean finish ({', '.join(bad)}); "
                          f"a receipt needs State=COMPLETED and ExitCode=0:0")
        return seen, None
    return None, (f"sacct: no main job line for job {job} (expected `{job}|COMPLETED|0:0` among the "
                  f"`{SACCT_FORMAT}` lines)")


# --------------------------------------------------------------------------
# the verify log

def check_self_hash(raw: bytes) -> str | None:
    """Refusal unless exactly one `verify-log-sha256: <hex>` line equals sha256(raw minus that line)."""
    lines = raw.split(b"\n")
    keep, found = [], []
    for i, line in enumerate(lines):
        m = _SHA_LINE.match(line)
        if m:
            found.append(m.group(1).decode().lower())
            continue
        keep.append(line + (b"\n" if i < len(lines) - 1 else b""))
    if not found:
        return f"verify log: no `{SHA_KEY}: <64 hex>` line"
    if len(found) > 1:
        return f"verify log: {len(found)} `{SHA_KEY}` lines; exactly one is allowed"
    actual = hashlib.sha256(b"".join(keep)).hexdigest()
    if actual != found[0]:
        return (f"verify log: {SHA_KEY} line says {found[0][:12]}…, but the log without that line hashes "
                f"to {actual[:12]}…; the log was changed after it was sealed")
    return None


def redact(text: str, extra: list[re.Pattern]) -> tuple[str, int]:
    """(text with the default per-job prefixes and every --redact match replaced, replacements made)."""
    n_total = 0
    for rx, repl in DEFAULT_REDACTIONS:
        text, n = re.subn(rx, repl, text)
        n_total += n
    for rx in extra:
        text, n = rx.subn(REDACT_TOKEN, text)
        n_total += n
    return text, n_total


def parse_fields(text: str) -> tuple[dict, list[str]]:
    """(key -> value, refusals) for the recognised `key: value` lines of the (redacted) log."""
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = _KEY_LINE.match(line.rstrip("\r"))
        if m and m.group(1) in REQUIRED_KEYS + OPTIONAL_KEYS:
            values.setdefault(m.group(1), []).append(m.group(2))
    out, refusals = {}, []
    for key in REQUIRED_KEYS + OPTIONAL_KEYS:
        got = values.get(key, [])
        if len(got) > 1:
            refusals.append(f"verify log: `{key}:` appears {len(got)} times; exactly one is allowed")
        elif got:
            out[key] = got[0]
        elif key in REQUIRED_KEYS:
            refusals.append(f"verify log: no `{key}:` line")
    for key in ("lean-toolchain", "mathlib-rev", "checker"):
        if key in out and not out[key]:
            refusals.append(f"verify log: `{key}:` is empty")
    if "sorry-count" in out:
        if not re.fullmatch(r"\d+", out["sorry-count"]):
            refusals.append(f"verify log: `sorry-count:` is not a non-negative integer: {out['sorry-count']!r}")
        elif int(out["sorry-count"]) != 0:
            refusals.append(f"verify log: sorry-count is {int(out['sorry-count'])}; gate requires 0")
    if "axioms" in out:
        out["axioms"] = parse_axioms(out["axioms"])
    if "generated-at" in out:
        try:
            core.parse_ts(out["generated-at"])
        except (ValueError, TypeError):
            refusals.append(f"verify log: `generated-at:` is not an ISO-8601 timestamp: {out['generated-at']!r}")
    return out, refusals


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


# --------------------------------------------------------------------------
# the command

def resolve_commit(repo: pathlib.Path, sha: str) -> str | None:
    p = core.git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def _scanner(repo: pathlib.Path) -> ps.Scanner:
    sc = ps.Scanner({}, repo, None)
    sc.emails = set()                  # a receipt declares no address: every one is a hit
    for problem in sc.problems:
        print(f"lean-pkg: WARN {problem}", file=sys.stderr)
    return sc


def blocking_families(sc: ps.Scanner, text: str) -> list[str]:
    return sorted({ps.PATTERNS_FILE if pat.kind == "file" else pat.label
                   for _n, pat, _ex in sc.scan_text(text) if pat.severity == ps.BLOCK})


def _shown(path: pathlib.Path, repo: pathlib.Path) -> str:
    """The path relative to the repository when inside it, else its file name (never an absolute path,
    which would carry a user name into the README line)."""
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


def _write(path: pathlib.Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def cmd_receipt_slurm(args) -> int:
    job = (args.job or "").strip()
    if not job:
        raise core.UsageError("--job must not be empty")
    slug = core.receipt_job_slug(job)
    repo = pathlib.Path(args.repo or ".").expanduser()
    if not repo.is_dir():
        raise core.UsageError(f"--repo {repo}: not a directory")
    log_path = pathlib.Path(args.log).expanduser()
    if not log_path.is_file():
        raise core.UsageError(f"--log {args.log}: no such file")
    if not args.commit_sha.strip() or args.commit_sha.startswith("-"):
        raise core.UsageError(f"--commit must name a commit, got {args.commit_sha!r}")
    extra = []
    for r in args.redact or []:
        try:
            extra.append(re.compile(r))
        except re.error as e:
            raise core.UsageError(f"--redact {r!r} is not a valid regular expression ({e.msg})") from None

    refusals: list[str] = []
    text, why = sacct_text(job, args.sacct_file)
    sacct = None
    if why:
        refusals.append(why)
    else:
        sacct, why = check_job(job, text)
        if why:
            refusals.append(why)

    raw = log_path.read_bytes()
    why = check_self_hash(raw)
    if why:
        refusals.append(why)
    redacted, n_redacted = redact(raw.decode("utf-8", errors="replace"), extra)
    fields, problems = parse_fields(redacted)
    refusals += problems

    commit = resolve_commit(repo, args.commit_sha)
    if commit is None:
        refusals.append(f"--commit {args.commit_sha}: not a commit in the --repo checkout "
                        f"(default the current directory)")
    else:
        blob = core.git_show_bytes(repo, "./lean-toolchain", commit)
        if blob is None:
            refusals.append(f"no lean-toolchain committed at {commit[:12]}")
        elif "lean-toolchain" in fields:
            committed = blob.decode("utf-8", "replace").strip()
            if fields["lean-toolchain"] != committed:
                refusals.append(f"toolchain mismatch: verify log says {fields['lean-toolchain']!r}, "
                                f"lean-toolchain committed at {commit[:12]} says {committed!r}")
        if "commit" in fields:
            claimed = fields["commit"].lower()
            if len(claimed) < 7 or not commit.startswith(claimed):
                refusals.append(f"verify log: `commit:` {fields['commit']!r} is not --commit {commit[:12]}")
    if "job-id" in fields and fields["job-id"] != job:
        refusals.append(f"verify log: `job-id:` {fields['job-id']!r} is not --job {job!r}")

    c12 = commit[:12] if commit else "0" * 12
    out = pathlib.Path(args.out).expanduser() if args.out else repo / ".lean-receipts" / f"{c12}-{slug}.json"
    log_out = out.with_name(f"{c12}-{slug}.verify.log")
    log_bytes = redacted.encode("utf-8")
    report_sha = hashlib.sha256(log_bytes).hexdigest()
    receipt = {
        "commit": commit,
        "lean_toolchain": fields.get("lean-toolchain"),
        "mathlib_rev": fields.get("mathlib-rev"),
        "sorry_count": int(fields["sorry-count"]) if re.fullmatch(r"\d+", str(fields.get("sorry-count", ""))) else None,
        "axioms": fields.get("axioms"),
        "report_sha256": report_sha,
        "job_id": job,
        "checker": fields.get("checker"),
        "generated_at": fields.get("generated-at") or core.now_iso(),
    }
    receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")

    sc = _scanner(repo)
    for what, blob_text in (("redacted verify log", redacted), ("receipt JSON", receipt_bytes.decode())):
        fams = blocking_families(sc, blob_text)
        if fams:
            refusals.append(f"public-safety: a private string remains in the {what} ({', '.join(fams)}); "
                            f"add a --redact REGEX for it")
    if not refusals:
        for path, data in ((log_out, log_bytes), (out, receipt_bytes)):
            why = _same_or_absent(path, data)
            if why:
                refusals.append(why)

    readme_line = next_cmd = None
    if not refusals:
        out.parent.mkdir(parents=True, exist_ok=True)
        _write(log_out, log_bytes)
        _write(out, receipt_bytes)
        rel_r, rel_l = _shown(out, repo), _shown(log_out, repo)
        axioms = ", ".join(receipt["axioms"]) or "none"
        readme_line = (f"- Lower-tier receipt: [`{rel_r}`]({rel_r}) (Slurm job {job}, commit {c12}, "
                       f"`{receipt['lean_toolchain']}`, sorry 0, axioms {axioms}; verify log [`{rel_l}`]({rel_l}), "
                       f"sha256 {report_sha[:12]}…)")
        next_cmd = f"lean-pkg gate NAME --check --receipt {rel_r}"

    if args.json_out:
        core.print_json({"ok": not refusals, "receipt": None if refusals else str(out),
                         "log": None if refusals else str(log_out),
                         "report_sha256": None if refusals else report_sha, "job_id": job,
                         "sacct": sacct, "redactions": n_redacted, "readme_line": readme_line,
                         "next": next_cmd, "refusals": refusals})
    if refusals:
        verdict = f"receipt slurm: refused, {len(refusals)} problem(s); nothing written"
        print(f"lean-pkg: {verdict}", file=sys.stderr)
        for r in refusals:
            print(f"  ✗ {r}", file=sys.stderr)
        print(f"lean-pkg: {verdict}", file=sys.stderr)
        return 1
    if not args.json_out:
        print(f"receipt slurm: ok, job {job} at commit {c12}; {n_redacted} redaction(s)")
        print(f"  receipt     {out}")
        print(f"  verify log  {log_out} (redacted; report_sha256 {report_sha})")
        print(f"README line to add:\n{readme_line}")
        print(f"next: {next_cmd}")
    allowed = set(core.DEFAULT_AXIOMS)
    if set(receipt["axioms"]) - allowed:
        print(f"lean-pkg: WARN axioms outside the default allowed set: "
              f"{sorted(set(receipt['axioms']) - allowed)}; gate refuses them unless the package's "
              f"gate.axioms_allowed lists them", file=sys.stderr)
    return 0


def add_parsers(sub, add_json) -> None:
    del add_json   # its --commit flag (committed mode) would collide with `receipt slurm --commit SHA`
    r = sub.add_parser("receipt", help="produce lower-tier receipts (writes files in the package checkout, "
                                       "never the registry)")
    rsub = r.add_subparsers(dest="receipt_cmd", metavar="{slurm}", required=True)
    s = rsub.add_parser("slurm", help="a lower-tier receipt from a finished Slurm job and its sealed verify log")
    s.add_argument("--job", required=True, metavar="J", help="the Slurm job id (the receipt's job_id)")
    s.add_argument("--log", required=True, metavar="PATH",
                   help=f"the job's verify log (key lines {', '.join(REQUIRED_KEYS)}, {SHA_KEY})")
    s.add_argument("--commit", dest="commit_sha", required=True, metavar="SHA",
                   help="the verified commit; must exist in --repo")
    s.add_argument("--repo", default=None, metavar="DIR", help="the package checkout (default: the current directory)")
    s.add_argument("--redact", action="append", default=[], metavar="REGEX",
                   help=f"also replace matches with {REDACT_TOKEN} (repeatable); the per-job /tmp/<name>-job<N>/ "
                        "and /scratch/<user>/ prefixes are always redacted")
    s.add_argument("--sacct-file", default=None, metavar="F",
                   help=f"saved output of `sacct -j J --format={SACCT_FORMAT} -P -n` (a host without Slurm)")
    s.add_argument("--out", default=None, metavar="PATH",
                   help="receipt path (default <repo>/.lean-receipts/<commit12>-<job>.json); the redacted log is "
                        "written beside it as <commit12>-<job>.verify.log")
    s.add_argument("--json", dest="json_out", action="store_true", default=argparse.SUPPRESS,
                   help="machine-readable JSON on stdout")
    s.set_defaults(fn=cmd_receipt_slurm)

