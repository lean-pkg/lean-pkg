# lean-pkg -- Lean 4 package registry: board, leases, trust gate, promotion
"""Manage Lean 4 formalization packages from one YAML registry.

The source of truth is a registry YAML file; GitHub (`gh`) is only the
visibility MIRROR. Every state transition appends BOTH a history[] entry in the
registry and one JSON line to the event log -- history is append-only, never
rewritten. See DESIGN.md for the lifecycle.

Registry resolution: --registry PATH, else env LEAN_PKG_REGISTRY, else the
user-level default ~/.lean-pkg/registry (a directory holding lean-packages.yaml,
or the registry file itself; base directory env LEAN_PKG_HOME), else
./lean-packages.yaml in the current directory. The event log is --events PATH,
else a sibling file <registry dir>/lean-package-events.jsonl.

A package's local_path is resolved RELATIVE to $HOME, so a registry can be
shared between machines that keep checkouts at the same home-relative place.
An absolute local_path is accepted transparently. Mutating operations that
touch the outside world are dry-run by default.

    lean-pkg [--registry PATH] [--events PATH] [--json] [--commit|--push|--no-commit] [subcommand]

    board     (default) curses TUI on a tty; static aligned table otherwise
    list      static table, pipe-friendly
    status    <name>                        full record + last 5 history entries
    init      [--force]                     bootstrap an empty registry + event log
    migrate   [--dry-run]                   upgrade the registry schema in place
    record    <name> EVENT --field K=V ...  publication evidence event, validated
                                            before writing (rules: `lean-pkg llms`)
    register  <name> --repo URL --path P    the ONLY write path for new packages;
              [--dry-run]                   validated, deduping, auto-detects
                                            toolchain + mathlib pin
    update    <name> [--notes ...] [--status S --reason T]
                                            fields of a registered package
    claim     <name> --workspace W --agent A [--ttl H] [--reason T] [--force]
    release   <name> [--agent A] [--force]
    renew     <name> --agent A [--ttl H]    restart your live lease; refuses once lapsed
    leases    [--agent A] [--expiring H]    held leases, soonest expiry first
    gate      <name> --receipt PATH         validate a lower-tier receipt, then
              [--comparator PATH]           gate-pending -> verified-private;
                                            optional comparator evidence;
                                            identical re-gate = no-op
    comparator <name> --receipt PATH        attach comparator evidence to a gated
              [--check]                     package; required before promote
    promote   <name> [--yes]                dry-run default; --yes flips GitHub
                                            visibility to public (ONE-WAY)
    audit     [--fix]                       report expired leases, gh visibility,
                                            paths, drift; --fix clears/reconciles
    validate                                schema + enum + history sanity
    doctor    [--fetch]                     install, registry source, leases, time,
                                            index and tool diagnostics
    index     add|check|render|migrate      public catalog <index>/<name>/entry.yaml
    claims    sync|list|check [NAME]        claims.yaml at the package repo root
    search    TERM [--record]               prior-art search (registry, index, web)
    formalization <name> [--write]          render formalization.yaml from the registry
    llms                                    agent-oriented usage contract
    wiki      [--out PATH]                  render a Markdown status page

--json emits machine-readable JSON on stdout (exit codes unchanged).
Time: every timestamp written is UTC ISO-8601 with offset, second precision; dates
are UTC dates; same-second events keep file order.
--commit (env LEAN_PKG_COMMIT=1) makes a write command pull --ff-only from origin
first, then commit the registry and the event log together; --push (env
LEAN_PKG_PUSH=1) also pushes; --no-commit overrides both. Read-only commands
ignore them; their reports go to stderr, so stdout is unchanged.
Everything is non-interactive: off a tty, no code path may block on input;
prompt paths refuse with exit 2.

Constraints enforced here:
  - agents never hand-edit the yaml: register is validated and deduping
  - one package = one workspace: CAS lease, 24h default TTL, audit --fix sweeps
  - the gate binds the receipt to HEAD; axioms must be a subset of
    {propext, Quot.sound, Classical.choice}; sorry_count must be 0
  - promotion requires the Reservoir-criteria checklist plus the trust gate
    and a comparator PASS; the GitHub flip is one-way and therefore dry-run
    by default
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import copy
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
import pathlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback
import urllib.error
import urllib.request

import yaml

from .holes import check_definition_holes
if __name__ == "__main__":
    # `python -m lean_pkg.cli`: lean_pkg.index imports this very module (one Fail class).
    sys.modules.setdefault("lean_pkg.cli", sys.modules[__name__])

HOME = pathlib.Path.home()
REGISTRY_ENV = "LEAN_PKG_REGISTRY"
REGISTRY_VERSION = 3                    # schema version written by `init` and `migrate`
SUPPORTED_REGISTRY_VERSIONS = (1, 2, 3) # readable by every command; older files lack the keys below
V2_PACKAGE_DEFAULTS = {
    "license_spdx": None,        # SPDX id detected or declared (ADR-0070 D5)
    "formalization_yaml": None,  # repo-relative path when the package has one
    "palomar": [],               # [{id, version, commit, comparator_config, registered_at, review_outcome, preflight_run}]
    "doi": None,                 # {"concept": str|None, "versions": [{"doi","tag","commit","minted_at"}]}
    "reservoir": None,           # {"listed_at": iso, "url": str} or None
    "consent": [],               # [{"who","role","recorded_at","pointer"}]; pointer is a private-repo path
}
V3_PACKAGE_DEFAULTS = {
    "claims_file": "claims.yaml",  # repo-relative path of the package's claims/1 file
    "claims": [],                  # last synced copy of that file's claims (see `claims sync`)
    "claims_synced_commit": None,  # 40-hex commit the cached claims were read at
}
# ADR-0070 D5 evidence events: name -> required --field keys. Each has a registry effect (see do_record).
EVIDENCE_EVENTS = {
    "consent-recorded":        ("who", "role", "pointer"),
    "palomar-preflight-pass":  ("commit", "run"),
    "palomar-submitted":       ("commit", "submission_id", "comparator_config"),
    "palomar-review":          ("commit", "outcome"),
    "palomar-registered":      ("commit", "id", "version", "comparator_config"),
    "palomar-withdrawn":       ("id", "version"),
    "doi-minted":              ("doi", "tag", "commit"),
    "reservoir-listed":        ("url",),
    "mathlib-pr-opened":       ("url",),
    "mathlib-pr-merged":       ("url",),
}
RETRACT_EVENT = "retract"   # `record NAME retract`: append-only correction of an evidence entry
_HEX40 = re.compile(r"[0-9a-f]{40}")
RESERVOIR_URL_RE = re.compile(r"^https://reservoir\.lean-lang\.org/@([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$")
RESERVOIR_API = "https://reservoir.lean-lang.org/api/v1/packages/{owner}/{name}"
DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
GITHUB_URL_RE = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+(?:/\S*)?")
PALOMAR_REGISTERED_STATUSES = ("public", "upstreamed")
PRIVATE_EVIDENCE_HINT = "private path in evidence; use a URL or repo-relative pointer"


def reservoir_listing_status(owner: str, name: str) -> int | None:
    """HTTP status of Reservoir's package endpoint (200 = listed, 404 = not listed),
    or None when the network is unreachable. Tests monkeypatch this function."""
    req = urllib.request.Request(RESERVOIR_API.format(owner=owner, name=name),
                                 headers={"User-Agent": "lean-pkg"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return int(r.status)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
DEFAULT_REGISTRY_NAME = "lean-packages.yaml"
EVENTS_NAME = "lean-package-events.jsonl"
NOTIFY_ENV = "LEAN_PKG_NOTIFY_CMD"

STATUSES = ["scratch", "active", "gate-pending", "verified-private",
            "public", "upstreamed", "archived"]
VISIBILITIES = ["private", "public"]
# register may only mint pre-evidence statuses; verified-private is gate's, public is promote's
# (register accepts public only for an already-public repository: --visibility public --reason).
REGISTER_STATUSES = ["scratch", "active", "gate-pending", "public"]
UPSTREAM_TARGETS = ["none", "mathlib", "palomar"]
DEFAULT_AXIOMS = ["propext", "Quot.sound", "Classical.choice"]
DEFAULT_TTL_HOURS = 24

REQUIRED_FIELDS = ["name", "repo", "local_path", "visibility", "status",
                   "owner_project", "workspace", "agent", "lease",
                   "lean_toolchain", "mathlib_pin", "upstream_target",
                   "automation", "gate", "promoted_at", "promoted_commit",
                   "reaudit_after", "notes", "history"]
LEASE_FIELDS = ["workspace", "agent", "claimed_at", "ttl_hours", "reason"]
RECEIPT_FIELDS = ["commit", "lean_toolchain", "mathlib_rev", "sorry_count",
                  "axioms", "report_sha256", "job_id", "checker",
                  "generated_at"]
COMPARATOR_SCHEMAS = ("comparator-receipt/1", "comparator-receipt/2")
COMPARATOR_SCHEMA = COMPARATOR_SCHEMAS[-1]
COMPARATOR_MECHANISMS = ("toolchain", "legacy")
COMPARATOR_TOOLCHAIN_DIGESTS = ["lake_sha256", "leanexport_sha256", "leanchecker_sha256",
                                "nanoda_bin_sha256", "con-ron_sha256"]
COMPARATOR_PASS_LINE = "Your solution is okay!"
# One acceptance predicate for both mechanisms, shared with the runner (comparator_receipt imports
# it). Observed live on 2026-10-06 (Lean v4.35.0-rc2): `lake comparator` prints the per-kernel lines
# ("con-ron kernel accepts the solution", "nanoda kernel accepts the solution", "Lean default kernel
# accepts the solution") and then the full line "Your solution is okay!"; the legacy stack prints the
# same final line. Only that full line counts.


def comparator_pass(log_text: str, mechanism: str) -> bool:
    """True when a comparator log shows acceptance: the full line COMPARATOR_PASS_LINE."""
    return COMPARATOR_PASS_LINE in log_text.splitlines()
COMPARATOR_TOOLS = ["comparator", "lean4export", "nanoda", "landrun"]
COMPARATOR_FIELDS = ["schema", "commit", "lean_toolchain", "tools", "host",
                     "job_id", "generated_at", "result", "configs"]
COMPARATOR_CONFIG_FIELDS = ["path", "sha256", "challenge_module",
                            "solution_module", "theorem_names",
                            "definition_names", "permitted_axioms", "log",
                            "log_sha256", "result"]
LICENSE_NAMES = ["LICENSE", "LICENSE.md", "LICENSE.txt",
                 "COPYING", "COPYING.md", "COPYING.txt"]

BOARD_HEADER = ("NAME", "STATUS", "VIS", "LEASE", "EXPIRES", "PIN", "REAUDIT")


class Fail(Exception):
    """User-facing refusal: printed to stderr, exit 1."""


class UsageError(Exception):
    """Interactive path attempted off-tty, or other usage-level error: exit 2."""


# --------------------------------------------------------------------------
# small utilities

# Time policy (one for every timestamp the tool writes): UTC, ISO-8601 with an explicit offset
# (+00:00), second precision. Dates the tool writes (wiki `updated:`, search headers, record file
# names) are the UTC date. Events written in the same second keep their order by position in
# history[] and the event log (file order is the tiebreak; there is no sequence field).
# A naive timestamp (legacy, hand-written) is read as UTC; validate accepts it, doctor counts it.

def now_iso() -> str:
    """Now, under the time policy: UTC, ISO-8601 with offset, second precision."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def utc_today() -> str:
    """Today's UTC date as YYYY-MM-DD (the time policy's date)."""
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def is_naive_ts(s) -> bool:
    """True for a parseable ISO-8601 timestamp without an offset (read as UTC by parse_ts)."""
    try:
        v = dt.datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return v.tzinfo is None


def naive_timestamps(reg: dict) -> int:
    """How many lease claimed_at / history ts values in the registry are naive (legacy)."""
    n = 0
    for pkg in (reg.get("packages") or {}).values():
        if not isinstance(pkg, dict):
            continue
        lease = pkg.get("lease")
        if isinstance(lease, dict) and lease.get("claimed_at") and is_naive_ts(lease["claimed_at"]):
            n += 1
        for h in pkg.get("history") or []:
            if isinstance(h, dict) and h.get("ts") and is_naive_ts(h["ts"]):
                n += 1
    return n


def parse_ts(s: str) -> dt.datetime:
    """Parse an ISO-8601 timestamp; a naive one is legacy and read as UTC (see the time policy)."""
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    v = dt.datetime.fromisoformat(s)
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.timezone.utc)
    return v


def run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """The one subprocess helper. Never raises on nonzero exit; a binary that cannot be run (not on
    PATH, not executable, a missing cwd) is returncode 127 with the OSError in stderr."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=cwd)
    except OSError as e:
        missing = isinstance(e, FileNotFoundError) and e.filename in (cmd[0], None) and shutil.which(cmd[0]) is None
        why = f"{cmd[0]} not on PATH" if missing else f"{cmd[0]}: {e.strerror or e}"
        return subprocess.CompletedProcess(cmd, 127, "", why)


def git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(repo), *args])


def gh(*args: str) -> subprocess.CompletedProcess:
    return run(["gh", *args])


def find_reuse() -> str | None:
    return shutil.which("reuse")


def notify(message: str) -> str:
    """Optional notification hook. If env LEAN_PKG_NOTIFY_CMD is set, run that
    command (split shell-style, no shell) with the message on stdin.

    Returns "sent" (exit 0), "failed" (nonzero exit or not runnable), or
    "not-configured" (variable unset or empty). Never raises."""
    cmd = os.environ.get(NOTIFY_ENV, "").strip()
    if not cmd:
        return "not-configured"
    try:
        argv = shlex.split(cmd)
        p = subprocess.run(argv, input=message + "\n", capture_output=True,
                           text=True, check=False)
    except (OSError, ValueError):
        return "failed"
    return "sent" if p.returncode == 0 else "failed"


def gh_nwo(repo_url: str) -> str | None:
    """owner/repo from a GitHub git URL (ssh or https), else None."""
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$",
                  str(repo_url).strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def repo_path(pkg: dict) -> pathlib.Path:
    # $HOME-relative by convention; an absolute local_path passes through.
    return HOME / pathlib.Path(str(pkg["local_path"]))


def git_head(repo: pathlib.Path) -> str | None:
    p = git(repo, "rev-parse", "HEAD")
    return p.stdout.strip() if p.returncode == 0 else None


def _rev(repo: pathlib.Path, ref: str) -> str | None:
    p = git(repo, "rev-parse", "--verify", "-q", ref + "^{commit}")
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def _is_ancestor(repo: pathlib.Path, a: str, b: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", a, b).returncode == 0


def reference_branch(repo: pathlib.Path) -> tuple[str | None, str | None, str]:
    """The branch a package's evidence is judged against, its tip, and a note; local refs only.
    Order: the remote default branch (refs/remotes/origin/HEAD), else `main`, else `master`.
    When both refs/heads/<b> and refs/remotes/origin/<b> exist, the tip is the one that contains
    the other (a fetched origin ahead of a never-pulled local branch wins); when they diverged,
    origin wins and the note says so. With no candidate branch, (None, HEAD, "").
    The registry has no per-package branch field, so none is consulted."""
    candidates = []
    p = git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if p.returncode == 0 and p.stdout.strip().startswith("refs/remotes/origin/"):
        candidates.append(p.stdout.strip()[len("refs/remotes/origin/"):])
    candidates += [b for b in ("main", "master") if b not in candidates]
    for b in candidates:
        local = _rev(repo, f"refs/heads/{b}")
        remote = _rev(repo, f"refs/remotes/origin/{b}")
        if not (local or remote):
            continue
        if not (local and remote) or local == remote:
            return b, local or remote, ""
        if _is_ancestor(repo, local, remote):
            return b, remote, ""
        if _is_ancestor(repo, remote, local):
            return b, local, ""
        return b, remote, f"local {b} and origin/{b} diverged; judged against origin/{b}"
    return None, git_head(repo), ""


def current_branch(repo: pathlib.Path) -> str:
    p = git(repo, "symbolic-ref", "-q", "--short", "HEAD")
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else "detached HEAD"


def evidence_only_descendant(repo: pathlib.Path, bound: str,
                             head: str) -> tuple[bool, str]:
    """Evidence-only descendant rule (see DESIGN.md): a receipt bound to commit `bound` is
    accepted at `head` when head == bound, or when head descends from bound and
    the only paths that differ live under .lean-receipts/ (the evidence itself).
    Paths are taken relative to the package directory `repo`: when the package is a
    subdirectory of its git repository (lean/, lean/contrib/), only changes under that
    directory count, and within it only <dir>/.lean-receipts/ is evidence.
    Returns (ok, note)."""
    if bound == head:
        return True, ""
    p = git(repo, "merge-base", "--is-ancestor", bound, head)
    if p.returncode != 0:
        return False, f"{bound[:12]} is not an ancestor of HEAD {head[:12]}"
    prefix = git(repo, "rev-parse", "--show-prefix").stdout.strip()
    # --relative: only paths under the package directory, relative to it.
    # --no-renames: a rename into .lean-receipts/ is a source change.
    p = git(repo, "diff", "--no-renames", "--name-only", "--relative", bound, head)
    paths = [x for x in p.stdout.splitlines() if x.strip()]
    foreign = [x for x in paths if not x.startswith(".lean-receipts/")]
    if prefix and not paths:
        return True, f"descendant of {bound[:12]} with no change under {prefix}"
    if foreign or not paths:
        return False, (f"HEAD {head[:12]} differs from {bound[:12]} outside "
                       f".lean-receipts/: {foreign[:5]}")
    return True, f"evidence-only descendant of {bound[:12]} (+{len(paths)} evidence file(s))"


def ensure_interactive() -> None:
    """Every prompt path must call this first: off a tty, refuse (exit 2)
    instead of blocking on input. Agents always use flags or --json."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise UsageError("interactive mode needs a tty on stdin+stdout; "
                         "use flags or --json instead")


def print_json(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def require_nonempty(flag: str, value) -> None:
    """Refuse an empty or whitespace-only value for a flag that names something (a path, a repo)."""
    if value is not None and not str(value).strip():
        raise Fail(f"{flag} must not be empty")


def local_path_key(stored) -> str:
    """Comparison form of a RECORDED local_path as this host resolves it: $HOME-joined (an absolute
    path passes through, as in repo_path), normalised, symlinks resolved."""
    return os.path.realpath(os.path.normpath(str(HOME / pathlib.Path(str(stored).strip()))))


def normalize_local_path(p, flag: str = "--path") -> tuple[str, str]:
    """The one normaliser for a --path given to register / update --path: (stored form, comparison
    key). A leading `~/` is expanded against $HOME (`~user` is refused); a relative path is taken
    relative to $HOME; the stored form is $HOME-relative when the path lies under $HOME, absolute
    otherwise, with `.`/`..`/`//`/trailing slashes collapsed. Empty or $HOME itself is refused."""
    require_nonempty(flag, p)
    s = str(p).strip()
    if s == "~" or s.startswith("~/"):
        s = str(HOME) + s[1:]
    elif s.startswith("~"):
        raise Fail(f"{flag} {p!r}: only a leading ~/ is expanded (to $HOME); give the path explicitly")
    home = os.path.normpath(str(HOME))
    absolute = os.path.normpath(os.path.join(home, s))
    if absolute == home:
        raise Fail(f"{flag} {p!r} resolves to $HOME itself; name the package checkout")
    rel = os.path.relpath(absolute, home)
    stored = absolute if rel == ".." or rel.startswith("../") else rel
    return stored, os.path.realpath(absolute)


def detect_toolchain(repo: pathlib.Path) -> tuple[str | None, str | None]:
    """(toolchain, warning) from the first line of <repo>/lean-toolchain."""
    f = repo / "lean-toolchain"
    if not f.is_file():
        return None, f"no lean-toolchain at {f}"
    lines = f.read_text().splitlines()
    if not lines or not lines[0].strip():
        return None, f"empty lean-toolchain at {f}"
    return lines[0].strip(), None


LEAN_LIB_RE = re.compile(r"^\s*lean_lib\s+(«[^»\n]+»|[A-Za-z_][A-Za-z0-9_'!?.]*)", re.MULTILINE)


def lean_root_namespaces(repo: pathlib.Path) -> set[str]:
    """Root namespaces (top-level module names) a checkout's Lean libraries claim, from the working
    tree: every `lean_lib` in lakefile.lean, else every [[lean_lib]] name in lakefile.toml, else the
    top-level *.lean files (lakefile.lean excluded). Empty when nothing is found or readable (#22.7)."""
    names: list[str] = []
    try:
        text = (repo / "lakefile.lean").read_text(encoding="utf-8", errors="replace")
        names = [m.group(1) for m in LEAN_LIB_RE.finditer(text)]
    except OSError:
        pass
    if not names:
        try:
            import tomllib
            data = tomllib.loads((repo / "lakefile.toml").read_text(encoding="utf-8"))
            libs = data.get("lean_lib") or []
            names = [str(lib["name"]) for lib in libs if isinstance(lib, dict) and lib.get("name")]
        except (OSError, ValueError, TypeError):
            pass
    if not names:
        try:
            names = [p.stem for p in repo.glob("*.lean") if p.name != "lakefile.lean"]
        except OSError:
            names = []
    return {n.strip("«»").split(".")[0] for n in names if n.strip("«»")}


def namespace_clashes(reg: dict, repo: pathlib.Path, skip: str | None = None) -> dict[str, list[str]]:
    """{root namespace: [other package names]} for the namespaces of `repo` that another registered
    package's checkout (on this host) also claims."""
    mine = lean_root_namespaces(repo) if repo.is_dir() else set()
    out: dict[str, list[str]] = {}
    if not mine:
        return out
    for other in sorted(reg.get("packages") or {}):
        pkg = reg["packages"][other] or {}
        if other == skip or not pkg.get("local_path"):
            continue
        try:
            theirs = repo_path(pkg)
            if theirs.resolve() == repo.resolve() or not theirs.is_dir():
                continue
        except (OSError, ValueError, TypeError):
            continue
        for ns in sorted(mine & lean_root_namespaces(theirs)):
            out.setdefault(ns, []).append(other)
    return out


def namespace_notes(clashes: dict[str, list[str]]) -> list[str]:
    return [f"note: Lean root namespace {ns} is also used by {', '.join(others)}; two packages with one "
            f"root namespace cannot be built or imported together (rename one lean_lib)"
            for ns, others in clashes.items()]


def detect_mathlib_pin(repo: pathlib.Path) -> tuple[str | None, str | None]:
    """(short pin, warning): rev of the mathlib package in lake-manifest.json."""
    mf = repo / "lake-manifest.json"
    if not mf.is_file():
        return None, "no lake-manifest.json — mathlib_pin set to null"
    try:
        data = json.loads(mf.read_text())
    except ValueError:
        return None, "lake-manifest.json is not valid JSON — mathlib_pin set to null"
    for p in data.get("packages") or []:
        if str(p.get("name", "")).lower() in ("mathlib", "«mathlib»"):
            rev = p.get("rev")
            if rev:
                return str(rev)[:10], None
            return None, "mathlib is a path dep or has no rev in " \
                          "lake-manifest.json — mathlib_pin set to null"
    return None, "no mathlib package in lake-manifest.json — mathlib_pin set to null"


# --------------------------------------------------------------------------
# registry IO, events

def registry_not_found(path: pathlib.Path) -> str:
    """The refusal for a missing registry: where the path came from and how to make one."""
    src = _RESOLVED_SOURCE.get(str(path))
    return (f"registry not found: {path} (" + (f"resolved from {src}; " if src else "")
            + f"create one with `lean-pkg init`, or set {REGISTRY_ENV} / ~/.lean-pkg/registry)")


def load_registry(path: pathlib.Path, *, check_version: bool = True) -> dict:
    if not path.is_file():
        raise Fail(registry_not_found(path))
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise Fail(f"registry is not valid YAML: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("packages"), dict):
        raise Fail(f"registry has no top-level 'packages' map: {path}")
    v = data.get("version")
    if check_version and isinstance(v, int) and not isinstance(v, bool) \
            and v > max(SUPPORTED_REGISTRY_VERSIONS):
        # A newer schema read by an older lean-pkg: refuse rather than show a silently partial view.
        raise Fail(f"registry schema version {v} is not supported (supported: "
                   f"{', '.join(map(str, SUPPORTED_REGISTRY_VERSIONS))}); this lean-pkg is older than the "
                   f"registry: upgrade lean-pkg (`lean-pkg migrate` upgrades only older registries)")
    return data


LOCK_SUFFIX = ".lock"
_LOCKS_HELD: dict[str, int] = {}   # lock file -> nesting depth held by THIS process


def lock_path(reg_path: pathlib.Path) -> pathlib.Path:
    """<registry>.lock, beside the registry file (a symlinked registry locks beside its target)."""
    p = pathlib.Path(reg_path).resolve()
    return p.with_name(p.name + LOCK_SUFFIX)


@contextlib.contextmanager
def registry_lock(reg_path: pathlib.Path):
    """Exclusive fcntl.flock on <registry>.lock for load -> check -> mutate -> save -> append.

    Every writer takes it, so concurrent processes on one host serialize; the lock file is created if
    missing and never deleted. Reentrant within a process (save_registry takes it too)."""
    lp = lock_path(reg_path)
    key = str(lp)
    if key in _LOCKS_HELD:
        _LOCKS_HELD[key] += 1
        try:
            yield lp
        finally:
            _LOCKS_HELD[key] -= 1
        return
    try:
        fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o666)
    except OSError as e:
        raise Fail(f"cannot open the registry lock {lp}: {e.strerror or e}")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _LOCKS_HELD[key] = 1
        try:
            yield lp
        finally:
            del _LOCKS_HELD[key]
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _umask() -> int:
    old = os.umask(0)
    os.umask(old)
    return old


def save_registry(path: pathlib.Path, data: dict) -> None:
    """Atomic save (private temp file in the same directory, fsync, os.replace; the old file's mode is
    kept), then the event lines record() queued on `data` are appended. Under the registry lock."""
    path = pathlib.Path(path)
    target = path.resolve() if path.is_symlink() else path
    text = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True, width=120)
    with registry_lock(target):
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            try:
                mode = stat.S_IMODE(os.stat(target).st_mode)
            except FileNotFoundError:
                mode = 0o666 & ~_umask()
            os.chmod(tmp, mode)
            os.replace(tmp, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        flush_events(data)


LEAN_PKG_HOME_ENV = "LEAN_PKG_HOME"


def lean_pkg_home() -> pathlib.Path:
    """The user-level base directory: $LEAN_PKG_HOME, else ~/.lean-pkg (read at call time, not import time)."""
    env = os.environ.get(LEAN_PKG_HOME_ENV, "").strip()
    return pathlib.Path(env).expanduser() if env else pathlib.Path.home() / ".lean-pkg"


def user_default_registry() -> tuple[pathlib.Path | None, str]:
    """(registry file or None, detail) for the user-level default <lean_pkg_home>/registry.

    A directory (or a symlink to one) holding lean-packages.yaml yields that file; a file (or a symlink to one) IS
    the registry file, resolved so that saves rewrite the target, not the link, and the event log lands beside it."""
    p = lean_pkg_home() / "registry"
    if p.is_dir():
        f = p / DEFAULT_REGISTRY_NAME
        if f.is_file():
            return f, f"{f}" + (f" ({p} -> {p.resolve()})" if p.is_symlink() else "")
        return None, f"{p} is a directory without {DEFAULT_REGISTRY_NAME}; not used"
    if p.is_file():
        f = p.resolve() if p.is_symlink() else p
        return f, f"{p}" + (f" -> {f}" if f != p else "") + " (registry file)"
    if p.is_symlink():
        return None, f"{p} is a dangling symlink; not used"
    return None, (f"{p} not configured (mkdir -p {p.parent} && ln -s /path/to/registry-repo {p}; "
                  f"base dir: env {LEAN_PKG_HOME_ENV})")


_RESOLVED_SOURCE: dict[str, str] = {}   # registry path -> how registry_source found it (for the not-found hint)


def registry_source(args) -> tuple[pathlib.Path, str]:
    """(registry path, source): --registry "flag", else $LEAN_PKG_REGISTRY "env", else the user-level default
    <lean_pkg_home>/registry "user-default", else ./lean-packages.yaml "cwd"."""
    if getattr(args, "registry", None):
        path, src = pathlib.Path(args.registry).expanduser(), "flag"
    elif os.environ.get(REGISTRY_ENV, "").strip():
        path, src = pathlib.Path(os.environ[REGISTRY_ENV].strip()).expanduser(), "env"
    else:
        user, _ = user_default_registry()
        path, src = (user, "user-default") if user is not None \
            else (pathlib.Path.cwd() / DEFAULT_REGISTRY_NAME, "cwd")
    _RESOLVED_SOURCE[str(path)] = src
    return path, src


def registry_path(args) -> pathlib.Path:
    """--registry, else $LEAN_PKG_REGISTRY, else ~/.lean-pkg/registry, else ./lean-packages.yaml."""
    return registry_source(args)[0]


EVENTS_ENV = "LEAN_PKG_EVENTS"


def events_source(args) -> tuple[pathlib.Path, str]:
    """(event log path, source): --events "flag"; else, when --registry is given, its sibling
    lean-package-events.jsonl "registry-sibling" (an explicit registry never writes into the log env
    LEAN_PKG_EVENTS names); else env LEAN_PKG_EVENTS "env"; else the resolved registry's sibling "sibling"."""
    if getattr(args, "events", None):
        return pathlib.Path(args.events).expanduser(), "flag"
    if getattr(args, "registry", None):
        return registry_path(args).parent / EVENTS_NAME, "registry-sibling"
    env = os.environ.get(EVENTS_ENV, "").strip()
    if env:
        return pathlib.Path(env).expanduser(), "env"
    return registry_path(args).parent / EVENTS_NAME, "sibling"


def events_path(args) -> pathlib.Path:
    """--events, else the sibling of an explicit --registry, else env LEAN_PKG_EVENTS, else the sibling
    lean-package-events.jsonl of the resolved registry."""
    return events_source(args)[0]


def _event_line(ts: str, name, event: str, **fields) -> str:
    return json.dumps({"ts": ts, "event": event, "name": name, **fields}, sort_keys=True) + "\n"


def _write_event_lines(events_p: pathlib.Path, lines: list[str]) -> None:
    events_p.parent.mkdir(parents=True, exist_ok=True)
    with open(events_p, "a", encoding="utf-8") as f:
        f.write("".join(lines))
        f.flush()
        os.fsync(f.fileno())


def append_event(events_p: pathlib.Path, ts: str, name, event: str, **fields) -> None:
    """Append one event line now: for events with no registry change to save (registry-created after
    the file is written, registry-migrated after the save, index-added, prior-art-searched)."""
    _write_event_lines(events_p, [_event_line(ts, name, event, **fields)])


# Event lines record() queued, per registry object (id -> (the object, [(events path, line)])); the
# object is held so its id cannot be reused. save_registry flushes them AFTER the YAML is replaced, so
# a failure before the save leaves no orphan event line.
_PENDING_EVENTS: dict[int, tuple[dict, list[tuple[pathlib.Path, str]]]] = {}


def flush_events(reg: dict) -> None:
    """Append the lines queued on `reg` (one write + fsync per event log) and forget them."""
    entry = _PENDING_EVENTS.pop(id(reg), None)
    if not entry:
        return
    by_path: dict[pathlib.Path, list[str]] = {}
    for p, line in entry[1]:
        by_path.setdefault(p, []).append(line)
    for p, lines in by_path.items():
        _write_event_lines(p, lines)


def record(reg: dict, events_p: pathlib.Path, pkg: dict, event: str, **fields) -> None:
    """One transition = one history[] entry + one event-log line. Append-only. The line is queued and
    written by the save_registry(reg) that persists the history entry, never before it."""
    ts = now_iso()
    pkg.setdefault("history", []).append({"ts": ts, "event": event, **fields})
    _PENDING_EVENTS.setdefault(id(reg), (reg, []))[1].append(
        (pathlib.Path(events_p), _event_line(ts, pkg.get("name"), event, **fields)))


# Validator rules a refusal names, keyed by a substring of the validator's message.
RULE_HINTS = {
    "matches no palomar entry": "rule doi-in-palomar: a doi-minted commit must already be recorded by a "
                                "palomar-* event on the package (record that first)",
}


def _err_key(e: str) -> str:
    """Comparison form of a validator message: a trailing parenthetical (a context listing) is dropped."""
    return re.sub(r" \([^()]*\)$", "", e)


def split_new_errors(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    """(errors this change introduces or repeats beyond the count before, pre-existing errors it leaves)."""
    left = collections.Counter(_err_key(e) for e in before)
    new, old = [], []
    for e in after:
        k = _err_key(e)
        if left[k] > 0:
            left[k] -= 1
            old.append(e)
        else:
            new.append(next((f"{e} [{h}]" for m, h in RULE_HINTS.items() if m in e), e))
    return new, old


def report_preexisting(errors: list[str]) -> None:
    """Pre-existing schema errors do not block an unrelated write; they are reported on stderr."""
    for e in errors:
        print(f"lean-pkg: pre-existing (not caused by this change): {e}", file=sys.stderr)


def _fingerprint(entry: dict) -> str:
    return json.dumps(entry, sort_keys=True, default=str)


def retracted_entries(pkg: dict) -> list[dict]:
    """Copies of the history entries that evidence-retracted entries retract (each stores its target verbatim,
    so a later entry with the same event and ts is not swept up)."""
    hist = pkg.get("history") if isinstance(pkg, dict) else None
    return [h["retracted_entry"] for h in (hist if isinstance(hist, list) else [])
            if isinstance(h, dict) and h.get("event") == "evidence-retracted"
            and isinstance(h.get("retracted_entry"), dict)]


def live_history(pkg: dict) -> list[dict]:
    """history[] without the entries an evidence-retracted entry retracts."""
    gone = {_fingerprint(e) for e in retracted_entries(pkg)}
    hist = pkg.get("history") if isinstance(pkg, dict) else None
    return [h for h in (hist if isinstance(hist, list) else []) if isinstance(h, dict)
            and _fingerprint(h) not in gone]


def get_pkg(reg: dict, name: str) -> dict:
    pkg = (reg.get("packages") or {}).get(name)
    if pkg is None:
        known = ", ".join(sorted(reg.get("packages") or {})) or "(none)"
        raise Fail(f"unknown package: {name!r} (known: {known})")
    return pkg


# --------------------------------------------------------------------------
# leases

def lease_expiry(lease: dict) -> dt.datetime:
    ttl = int(lease.get("ttl_hours") or DEFAULT_TTL_HOURS)
    return parse_ts(lease["claimed_at"]) + dt.timedelta(hours=ttl)


LEASE_PARSE_ERRORS = (TypeError, ValueError, KeyError, AttributeError, OverflowError)


def lease_expiry_or_none(lease) -> dt.datetime | None:
    """lease_expiry, or None when the lease's fields cannot be parsed (a hand-edited claimed_at: zzz or
    ttl_hours: 'x'): readers render such a lease as INVALID instead of raising."""
    try:
        return lease_expiry(lease)
    except LEASE_PARSE_ERRORS:
        return None


def lease_invalid(lease) -> bool:
    return bool(lease) and lease_expiry_or_none(lease) is None


def refuse_invalid_lease(name: str, lease) -> None:
    """Writers refuse a package whose lease cannot be parsed; `release --force` is the way out."""
    if lease_invalid(lease):
        get = lease.get if isinstance(lease, dict) else (lambda _k: None)
        raise Fail(f"{name}: lease fields unparseable (claimed_at={get('claimed_at')!r}, "
                   f"ttl_hours={get('ttl_hours')!r}); clear it with "
                   f"`lean-pkg release {name} --force --reason TEXT`")


def lease_live(lease: dict | None, now: dt.datetime) -> bool:
    """True while a lease runs. Raises on unparseable fields: writers call refuse_invalid_lease first,
    readers use lease_expiry_or_none."""
    return bool(lease) and now < lease_expiry(lease)


def lease_live_or_none(lease, now: dt.datetime) -> bool | None:
    """lease_live for readers: None when the lease cannot be parsed."""
    exp = lease_expiry_or_none(lease) if lease else None
    if lease and exp is None:
        return None
    return bool(lease) and now < exp


def human_delta(seconds: float) -> str:
    """Compact duration: 45s, 12m, 2h13m, 1d2h (largest two units)."""
    s = int(abs(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d{h}h" if h else f"{d}d"
    if h:
        return f"{h}h{m}m" if m else f"{h}h"
    if m:
        return f"{m}m"
    return f"{s}s"


def lease_expires_label(lease: dict | None, now: dt.datetime) -> str:
    """Board cell: `in 2h13m` while the lease is live, `3h ago` once it has lapsed
    (until `audit --fix` sweeps it), `-` without a lease."""
    if not lease:
        return "-"
    exp = lease_expiry_or_none(lease)
    if exp is None:
        return "?"
    delta = (exp - now).total_seconds()
    return f"in {human_delta(delta)}" if delta > 0 else f"{human_delta(delta)} ago"


LEASE_WARN_HOURS = 2    # doctor warns about leases that lapse within this window


def nonneg_hours(v: str) -> float:
    """argparse type for an hours window: a number >= 0 (a negative window is a usage error, exit 2)."""
    try:
        h = float(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of hours: {v!r}")
    if h < 0 or h != h:
        raise argparse.ArgumentTypeError(f"hours must be >= 0, got {v}")
    return h


def lease_rows(reg: dict, now: dt.datetime, agent: str | None = None,
               expiring_hours: float | None = None) -> list[dict]:
    """Every held lease as a dict (name, agent, workspace, claimed_at, ttl_hours, expires_at, live,
    remaining_seconds, invalid), soonest expiry first; filtered by holder and by `expires within N hours
    or already lapsed`. A lease whose fields cannot be parsed is listed first with invalid: true,
    expires_at and remaining_seconds null, live false (it always passes the --expiring filter)."""
    rows = []
    for name in sorted(reg.get("packages", {})):
        lease = reg["packages"][name].get("lease")
        if not lease:
            continue
        get = lease.get if isinstance(lease, dict) else (lambda _k: None)
        if agent is not None and get("agent") != agent:
            continue
        exp = lease_expiry_or_none(lease)
        remaining = (exp - now).total_seconds() if exp is not None else None
        if remaining is not None and expiring_hours is not None and remaining > expiring_hours * 3600:
            continue
        rows.append({"name": name, "agent": get("agent"), "workspace": get("workspace"),
                     "claimed_at": get("claimed_at"), "ttl_hours": get("ttl_hours"),
                     "expires_at": exp.isoformat() if exp is not None else None,
                     "live": remaining is not None and remaining > 0,
                     "remaining_seconds": int(remaining) if remaining is not None else None,
                     "invalid": exp is None})
    rows.sort(key=lambda r: (not r["invalid"], r["remaining_seconds"] or 0))
    return rows


LEASES_HEADER = ("NAME", "AGENT", "WORKSPACE", "CLAIMED", "EXPIRES", "STATE")


def leases_lines(rows: list[dict], now: dt.datetime) -> list[str]:
    def expires(r):
        if r.get("invalid"):
            return "?"
        return (f"in {human_delta(r['remaining_seconds'])}" if r["live"]
                else f"{human_delta(r['remaining_seconds'])} ago")

    def state(r):
        if r.get("invalid"):
            return "INVALID (release --force --reason clears)"
        return "live" if r["live"] else "EXPIRED (audit --fix sweeps)"

    table = [(r["name"], str(r["agent"]), str(r["workspace"] or "-"), str(r["claimed_at"])[:16],
              expires(r), state(r)) for r in rows]
    widths = [max([len(LEASES_HEADER[i])] + [len(t[i]) for t in table]) for i in range(len(LEASES_HEADER))]
    fmt = lambda t: "  ".join(t[i].ljust(widths[i]) for i in range(len(LEASES_HEADER))).rstrip()
    return [fmt(LEASES_HEADER)] + [fmt(t) for t in table]


def leases_summary(reg: dict, now: dt.datetime) -> tuple[str, str]:
    """(level, detail) for doctor: live / expiring within LEASE_WARN_HOURS / expired-unswept counts with names."""
    rows = lease_rows(reg, now)
    bad = [r for r in rows if r["invalid"]]
    live = [r for r in rows if r["live"]]
    soon = [r for r in live if r["remaining_seconds"] <= LEASE_WARN_HOURS * 3600]
    gone = [r for r in rows if not r["live"] and not r["invalid"]]
    parts = [f"{len(live)} live"]
    if soon:
        parts.append(f"{len(soon)} expiring within {LEASE_WARN_HOURS}h: "
                     + ", ".join(f"{r['name']} ({r['agent']}, in {human_delta(r['remaining_seconds'])})" for r in soon))
    if gone:
        parts.append(f"{len(gone)} expired, not swept: "
                     + ", ".join(f"{r['name']} ({r['agent']}, {human_delta(r['remaining_seconds'])} ago)" for r in gone)
                     + " (audit --fix clears)")
    if bad:
        parts.append(f"{len(bad)} invalid (fields unparseable): " + ", ".join(r["name"] for r in bad)
                     + " (release NAME --force --reason TEXT clears)")
    return ("warn" if soon or gone or bad else "ok"), "; ".join(parts)


def with_expiry(reg: dict) -> dict:
    """A copy of the registry whose live lease mappings also carry `expires_at` (ISO, UTC);
    for --json consumers so nobody recomputes claimed_at + ttl_hours."""
    import copy
    out = copy.deepcopy(reg)
    for pkg in out.get("packages", {}).values():
        lease = pkg.get("lease")
        if isinstance(lease, dict) and lease:
            exp = lease_expiry_or_none(lease)
            lease["expires_at"] = exp.isoformat() if exp is not None else None
    return out


def refresh_lease(reg_path, reg, events_p, pkg, lease, agent, ttl, workspace, reason):
    """Restart a live lease held by `agent` from now with `ttl` hours; event claim-refreshed (with the
    reason when one is given). Callers pass the lease's own ttl_hours when no --ttl was given."""
    name = pkg["name"]
    lease["claimed_at"] = now_iso()
    lease["ttl_hours"] = ttl
    if workspace:
        lease["workspace"] = workspace
    if reason:
        lease["reason"] = reason
    pkg["workspace"] = lease.get("workspace")
    record(reg, events_p, pkg, "claim-refreshed", agent=agent,
           workspace=lease.get("workspace"), ttl_hours=ttl, **({"reason": reason} if reason else {}))
    save_registry(reg_path, reg)
    payload = {"ok": True, "name": name, "event": "claim-refreshed",
               "lease": dict(lease),
               "expires_at": lease_expiry(lease).isoformat()}
    return ([f"{name}: lease refreshed for {agent} "
             f"until {lease_expiry(lease).isoformat()}"], payload)


def do_renew(reg_path, reg, events_p, name, agent, ttl, workspace, reason):
    """Holder-only refresh that refuses when there is nothing to renew: no lease, another
    holder, or a lease that has already lapsed (claim again instead; `audit --fix` sweeps it)."""
    if ttl is not None and ttl <= 0:
        raise Fail("--ttl must be a positive number of hours")
    pkg = get_pkg(reg, name)
    now = dt.datetime.now(dt.timezone.utc)
    lease = pkg.get("lease")
    if not lease:
        raise Fail(f"{name}: no lease held — claim it: lean-pkg claim {name} --workspace W --agent {agent}")
    refuse_invalid_lease(name, lease)
    if lease.get("agent") != agent:
        raise Fail(f"{name}: lease held by {lease.get('agent')}, not {agent} — renew is holder-only "
                   f"(take it over with claim --force --reason)")
    if not lease_live(lease, now):
        raise Fail(f"{name}: lease by {agent} expired at {lease_expiry(lease).isoformat()} "
                   f"({human_delta((now - lease_expiry(lease)).total_seconds())} ago) — not renewable; "
                   f"claim it again (audit --fix sweeps the expired lease)")
    ttl = ttl or int(lease.get("ttl_hours") or DEFAULT_TTL_HOURS)
    return refresh_lease(reg_path, reg, events_p, pkg, lease, agent, ttl, workspace, reason)


def do_claim(reg_path, reg, events_p, name, workspace, agent, ttl, reason, force):
    """`ttl` None: a fresh claim gets DEFAULT_TTL_HOURS and the holder's re-claim (a refresh) keeps the
    lease's current ttl_hours, as renew does."""
    if ttl is not None and ttl <= 0:
        raise Fail("--ttl must be a positive number of hours")
    if force and not reason:
        raise Fail("--force requires --reason")
    pkg = get_pkg(reg, name)
    now = dt.datetime.now(dt.timezone.utc)
    lease = pkg.get("lease")
    refuse_invalid_lease(name, lease)
    if lease and lease_live(lease, now):
        if lease.get("agent") == agent and not force:
            ttl = ttl or int(lease.get("ttl_hours") or DEFAULT_TTL_HOURS)
            return refresh_lease(reg_path, reg, events_p, pkg, lease, agent, ttl, workspace, reason)
        if not force:
            raise Fail(f"{name}: lease held by {lease.get('agent')} "
                       f"(workspace {lease.get('workspace')}) until "
                       f"{lease_expiry(lease).isoformat()} — refusing; "
                       f"override with --force --reason")
        event = "claim-forced"
    else:
        event = "claim-forced" if force else "claimed"
    ttl = ttl or DEFAULT_TTL_HOURS
    pkg["lease"] = {"workspace": workspace, "agent": agent,
                    "claimed_at": now_iso(), "ttl_hours": ttl,
                    "reason": reason or ""}
    pkg["workspace"] = workspace
    pkg["agent"] = agent
    record(reg, events_p, pkg, event, agent=agent, workspace=workspace,
           ttl_hours=ttl, reason=reason or "")
    save_registry(reg_path, reg)
    payload = {"ok": True, "name": name, "event": event,
               "lease": dict(pkg["lease"]),
               "expires_at": lease_expiry(pkg["lease"]).isoformat()}
    return ([f"{name}: lease {event} for {agent}@{workspace} "
             f"until {lease_expiry(pkg['lease']).isoformat()}"], payload)


def do_release(reg_path, reg, events_p, name, agent, force, reason=""):
    """Holder-only unless forced; --force needs a reason. released_by is the acting --agent, else
    "force" (the event carries the reason either way)."""
    pkg = get_pkg(reg, name)
    lease = pkg.get("lease")
    if not lease:
        raise Fail(f"{name}: no lease held")
    if force and not reason:
        raise Fail(f"{name}: --force requires --reason")
    if not force:
        refuse_invalid_lease(name, lease)
    if not isinstance(lease, dict):
        lease = {}   # an unparseable non-mapping lease: release --force clears it (never calls lease_live)
    if force:
        event = "release-forced"
    elif agent is None:
        raise Fail(f"{name}: lease held by {lease.get('agent')} — "
                   f"pass --agent {lease.get('agent')} or --force")
    elif agent != lease.get("agent"):
        raise Fail(f"{name}: lease held by {lease.get('agent')}, not {agent} — "
                   f"holder-only (or --force)")
    else:
        event = "released"
    record(reg, events_p, pkg, event, agent=lease.get("agent"),
           workspace=lease.get("workspace"), released_by=agent or "force", reason=reason or "")
    pkg["lease"] = None
    pkg["workspace"] = None
    pkg["agent"] = None
    save_registry(reg_path, reg)
    payload = {"ok": True, "name": name, "event": event,
               "released_lease": dict(lease)}
    return ([f"{name}: lease {event} "
             f"(was {lease.get('agent')}@{lease.get('workspace')})"], payload)


# --------------------------------------------------------------------------
# prior-art note at claim (advisory, never a gate; spec D4)

PRIOR_ART_WINDOW_DAYS = 30


def recent_prior_art(events_p: pathlib.Path, name: str, now: dt.datetime,
                     days: int = PRIOR_ART_WINDOW_DAYS) -> bool:
    """True when the event log holds a prior-art-searched line for `name` within `days`."""
    if not events_p.is_file():
        return False
    cutoff = now - dt.timedelta(days=days)
    for line in events_p.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
            if isinstance(e, dict) and e.get("event") == "prior-art-searched" \
                    and e.get("name") == name and parse_ts(e.get("ts")) >= cutoff:
                return True
        except (ValueError, TypeError):
            continue
    return False


def prior_art_note(pkg: dict, events_p: pathlib.Path, now: dt.datetime | None = None) -> str | None:
    """The advisory line `claim` prints when a package has neither claims (cached, or a claims
    file in its checkout) nor a prior-art search in the last PRIOR_ART_WINDOW_DAYS days."""
    if pkg.get("claims"):
        return None
    rel = pkg.get("claims_file") or "claims.yaml"
    if pkg.get("local_path") and (repo_path(pkg) / rel).is_file():
        return None
    name = pkg.get("name")
    if recent_prior_art(events_p, name, now or dt.datetime.now(dt.timezone.utc)):
        return None
    return (f"note: {name} has no prior-art search recorded in the last {PRIOR_ART_WINDOW_DAYS} days "
            f"and no {rel}; before building, run `lean-pkg search TERM --package {name} --record` "
            f"(advisory)")


# --------------------------------------------------------------------------
# update: the write path for fields of an ALREADY-REGISTERED package

# Fields `update` may set. Everything else is owned by a dedicated subcommand
# and is refused by name, so `update` can never become a way around the trust
# gate: `gate`/`promoted_at`/`promoted_commit`/`visibility` belong to `gate`
# and `promote`, and `lease`/`workspace`/`agent` to `claim`/`release`.
UPDATABLE = {"automation", "notes", "upstream_target", "reaudit_after",
             "owner_project", "repo", "license_spdx", "formalization_yaml", "paper"}
# Paper-coupled packages (#22.10): optional `paper: {arxiv, status}`; promote refuses while the paper is
# unpublished and no arXiv id is recorded (so a public flip cannot disclose results before the preprint).
PAPER_STATUSES = ("unpublished", "preprint", "published")
PAPER_KEYS = ("arxiv", "status")
ARXIV_ID_RE = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")


def paper_errors(paper, pfx: str) -> list[str]:
    if paper is None:
        return []
    if not isinstance(paper, dict):
        return [f"{pfx}.paper: must be a mapping {{arxiv, status}} or null"]
    errors = [f"{pfx}.paper: unknown key {k!r} (allowed: {', '.join(PAPER_KEYS)})"
              for k in paper if k not in PAPER_KEYS]
    if paper.get("status") not in PAPER_STATUSES:
        errors.append(f"{pfx}.paper.status: must be one of {', '.join(PAPER_STATUSES)}, "
                      f"got {paper.get('status')!r}")
    ax = paper.get("arxiv")
    if ax is not None and not (isinstance(ax, str) and ARXIV_ID_RE.match(ax)):
        errors.append(f"{pfx}.paper.arxiv: must be an arXiv id such as 2410.01234 or math/0601001, "
                      f"got {ax!r}")
    return errors


def paper_blocks_promote(pkg: dict) -> bool:
    paper = pkg.get("paper")
    return isinstance(paper, dict) and paper.get("status") == "unpublished" and not paper.get("arxiv")
SPDX_ID_RE = re.compile(r"^[A-Za-z0-9.+-]+$")
SPDX_OPERATORS = ("AND", "OR", "WITH")


def spdx_expression_ok(value) -> bool:
    """A small SPDX license-expression grammar: identifiers (each matching SPDX_ID_RE) joined by
    AND / OR, an identifier optionally followed by WITH <exception id>, and parentheses.
      expr := term (("AND" | "OR") term)* ; term := atom ["WITH" id] ; atom := id | "(" expr ")" """
    tokens = re.findall(r"\(|\)|[^\s()]+", str(value))
    pos = 0

    def ident() -> bool:
        nonlocal pos
        if pos < len(tokens) and tokens[pos] not in SPDX_OPERATORS + ("(", ")") \
                and SPDX_ID_RE.match(tokens[pos]):
            pos += 1
            return True
        return False

    def atom() -> bool:
        nonlocal pos
        if pos < len(tokens) and tokens[pos] == "(":
            pos += 1
            if not expr() or pos >= len(tokens) or tokens[pos] != ")":
                return False
            pos += 1
            return True
        return ident()

    def term() -> bool:
        nonlocal pos
        if not atom():
            return False
        if pos < len(tokens) and tokens[pos] == "WITH":
            pos += 1
            return ident()
        return True

    def expr() -> bool:
        nonlocal pos
        if not term():
            return False
        while pos < len(tokens) and tokens[pos] in ("AND", "OR"):
            pos += 1
            if not term():
                return False
        return True

    return bool(tokens) and expr() and pos == len(tokens)
OWNED_ELSEWHERE = {
    "gate": "lean-pkg gate", "promoted_at": "lean-pkg promote",
    "promoted_commit": "lean-pkg promote", "visibility": "lean-pkg promote",
    "lease": "lean-pkg claim/release", "workspace": "lean-pkg claim",
    "agent": "lean-pkg claim", "status": "lean-pkg update --status",
    "name": "(immutable)", "local_path": "lean-pkg update NAME --path NEW --reason TEXT",
    "history": "(append-only)",
}

# Status moves `update` may make. The registry's other transitions have
# evidence attached and belong to the subcommand that checks that evidence:
# verified-private is `gate`'s (it validates a lower-tier receipt) and public
# is `promote`'s (it runs the checklist and flips gh visibility).
MANUAL_TRANSITIONS = {
    ("scratch", "active"),
    ("active", "gate-pending"),      # a lower-tier check run was submitted
    ("gate-pending", "active"),       # ... and was abandoned
    ("verified-private", "gate-pending"),  # re-certification after content
                                           # change; `gate` still requires a
                                           # fresh receipt for the new commit
    ("scratch", "archived"), ("active", "archived"),
    ("gate-pending", "archived"), ("verified-private", "archived"),
    ("public", "upstreamed"),        # needs upstream evidence or a --reason URL
                                     # (see upstream_evidence); public stays
                                     # one-way: no move back to a private status
    ("public", "archived"), ("upstreamed", "archived"),
}
# Status moves a dedicated subcommand makes; a refusal names them next to the manual moves.
SUBCOMMAND_TRANSITIONS = {
    ("gate-pending", "verified-private"): "lean-pkg gate --receipt R",
    ("verified-private", "public"): "lean-pkg promote --yes",
}
UPSTREAM_EVIDENCE_EVENTS = ("mathlib-pr-merged", "palomar-registered")
URL_RE = re.compile(r"https?://\S+")


def allowed_moves(old) -> str:
    """The moves out of status `old`, for a refusal: manual ones, then the subcommands' ones."""
    moves = sorted(b for a, b in MANUAL_TRANSITIONS if a == old)
    moves += [f"{b} (via {cmd})" for (a, b), cmd in SUBCOMMAND_TRANSITIONS.items() if a == old]
    return ", ".join(moves) or "none"


def upstream_evidence(pkg: dict) -> list[str]:
    """Unretracted mathlib-pr-merged / palomar-registered events (a withdrawn Palomar entry does not count)."""
    hist = live_history(pkg)
    withdrawn = {(str(h.get("id")), str(h.get("version"))) for h in hist if h.get("event") == "palomar-withdrawn"}
    out = []
    for h in hist:
        if h.get("event") == "mathlib-pr-merged":
            out.append(f"mathlib-pr-merged {h.get('url')}")
        elif h.get("event") == "palomar-registered" \
                and (str(h.get("id")), str(h.get("version"))) not in withdrawn:
            out.append(f"palomar-registered {h.get('id')} v{h.get('version')}")
    return out


def do_update(reg_path, reg, events_p, name, *, sets, status, reason,
              redetect, agent, force, dry_run=False):
    """Set fields of a registered package. dry_run runs every check (lease, transitions, schema) on a copy
    and writes nothing: callers that touch other files first (formalization --write) refuse before them."""
    if dry_run:
        reg = copy.deepcopy(reg)
    pkg = get_pkg(reg, name)

    for field in sets:
        if field in OWNED_ELSEWHERE:
            raise Fail(f"{name}: field {field!r} is not settable here — "
                       f"use {OWNED_ELSEWHERE[field]}")
        if field not in UPDATABLE:
            raise Fail(f"{name}: unknown field {field!r} "
                       f"(settable: {', '.join(sorted(UPDATABLE))})")
        value = sets[field]
        if field in ("repo", "owner_project"):
            require_nonempty("--" + field.replace("_", "-"), value)
        if field == "license_spdx" and not spdx_expression_ok(value):
            raise Fail(f"license_spdx must be an SPDX identifier such as Apache-2.0 or MIT, or an SPDX "
                       f"expression such as 'MIT OR Apache-2.0', got {value!r}")
        if field == "paper":   # a partial {arxiv?, status?}, merged into the recorded one
            merged = dict(pkg.get("paper") or {})
            merged.update(value)
            if "status" not in merged or ("arxiv" in value and "status" not in value
                                          and merged["status"] == "unpublished"):
                merged["status"] = "preprint" if merged.get("arxiv") else "unpublished"
            merged.setdefault("arxiv", None)
            errs = paper_errors(merged, name)
            if errs:
                raise Fail("; ".join(errs))
            sets[field] = {k: merged[k] for k in PAPER_KEYS}
        if field == "formalization_yaml":
            pp = pathlib.PurePosixPath(str(value))
            if pp.is_absolute() or ".." in pp.parts or str(value).startswith("~"):
                raise Fail(f"formalization_yaml must be a repository-relative path, got {value!r}")

    # Lease discipline, the same rule `release` applies: a live lease held by
    # someone else blocks a write unless it is forced with a reason.
    lease = pkg.get("lease")
    refuse_invalid_lease(name, lease)
    if lease_live(lease, dt.datetime.now(dt.timezone.utc)):
        holder = lease.get("agent")
        if not force and agent != holder:
            raise Fail(f"{name}: lease held by {holder} — pass --agent {holder}, "
                       f"or --force with --reason")
    if force and not reason:
        raise Fail(f"{name}: --force requires --reason")

    changes: dict[str, tuple] = {}

    if redetect:
        repo = repo_path(pkg)
        if not repo.exists():
            raise Fail(f"{name}: local_path does not exist: {repo}")
        tool, _ = detect_toolchain(repo)
        pin, _ = detect_mathlib_pin(repo)
        for field, value in (("lean_toolchain", tool), ("mathlib_pin", pin)):
            if value and pkg.get(field) != value:
                changes[field] = (pkg.get(field), value)

    for field, value in sets.items():
        if pkg.get(field) != value:
            changes[field] = (pkg.get(field), value)

    if status is not None:
        old = pkg.get("status")
        if status not in STATUSES:
            raise Fail(f"bad status {status!r} (allowed: {', '.join(STATUSES)})")
        if status != old:
            if not reason:
                raise Fail(f"{name}: --status requires --reason")
            if (old, status) not in MANUAL_TRANSITIONS:
                hint = {"verified-private": "lean-pkg gate --receipt R",
                        "public": "lean-pkg promote --yes"}.get(status)
                raise Fail(f"{name}: {old} -> {status} is not a manual transition"
                           + (f" — use {hint}" if hint else "")
                           + f"; allowed from {old}: {allowed_moves(old)}")
            if status == "upstreamed" and not upstream_evidence(pkg) and not URL_RE.search(reason):
                raise Fail(f"{name}: public -> upstreamed needs evidence: a recorded "
                           f"{' or '.join(UPSTREAM_EVIDENCE_EVENTS)} event (`lean-pkg record {name} "
                           f"mathlib-pr-merged --field url=URL`), or a --reason containing the merged PR / "
                           f"Palomar URL (http...)")
            changes["status"] = (old, status)

    if not changes:
        # Idempotent: no history entry, no event, no write. `gate` behaves the
        # same way for an already-recorded receipt.
        payload = {"ok": True, "name": name, "event": "update-noop", "changes": {}}
        return ([f"{name}: already up to date; nothing written"], payload)

    before = validate_registry(reg)
    for field, (_old, new) in changes.items():
        pkg[field] = new

    errors, preexisting = split_new_errors(before, validate_registry(reg))
    if errors:
        # Never persist a schema error this change introduces; errors already in the
        # registry (another package's, say) are reported and do not block.
        raise Fail(f"{name}: update would break the registry schema:\n  "
                   + "\n  ".join(errors))
    if dry_run:
        return ([f"{name}: would update {len(changes)} field(s); nothing written"],
                {"ok": True, "name": name, "event": "update-dry-run",
                 "changes": {f: {"from": o, "to": n} for f, (o, n) in changes.items()}})
    report_preexisting(preexisting)

    record(reg, events_p, pkg, "updated",
           fields=sorted(changes), reason=reason or "", forced=bool(force),
           **{f"{f}_from": o for f, (o, _n) in changes.items()})
    save_registry(reg_path, reg)

    out = [f"{name}: updated {len(changes)} field(s)"]
    for field, (old, new) in sorted(changes.items()):
        out.append(f"  {field}: {old!r} -> {new!r}")
    payload = {"ok": True, "name": name, "event": "updated",
               "changes": {f: {"from": o, "to": n} for f, (o, n) in changes.items()},
               "reason": reason or "", "forced": bool(force)}
    if preexisting:
        payload["preexisting_errors"] = preexisting
    return (out, payload)


def _bound_at(repo: pathlib.Path, head: str | None, rel) -> tuple[bool, str | None]:
    """(kept, bound commit) for a recorded receipt path at the new checkout: the file must be
    readable there and its commit must equal HEAD (or HEAD an evidence-only descendant of it)."""
    rec = recorded_json(repo, rel)
    commit = str((rec or {}).get("commit") or "") or None
    if not (head and commit and _HEX40.fullmatch(commit)):
        return False, commit
    return evidence_only_descendant(repo, commit, head)[0], commit


def do_relocate(reg_path, reg, events_p, name, *, new_path, reason, agent,
                redetect, dry_run) -> tuple[list[str], dict]:
    """update NAME --path NEW: move a package's local_path, keeping its name, history and
    claims cache. Never touches the file system; anything bound to a checkout commit that
    cannot be shown to hold at the new checkout is cleared, and the event lists it."""
    pkg = get_pkg(reg, name)
    if not reason:
        raise Fail(f"{name}: --path requires --reason (why the package moved)")
    new, new_key = normalize_local_path(new_path)
    old = pkg.get("local_path")
    if old and local_path_key(old) == new_key:
        # Idempotent, like update's other no-ops: no history entry, no event, no write.
        payload = {"ok": True, "name": name, "event": "update-noop", "changes": {},
                   "local_path": old, "dry_run": bool(dry_run)}
        return [f"{name}: already at {old}; nothing written"], payload
    for other, opkg in (reg.get("packages") or {}).items():
        if other != name and opkg.get("local_path") \
                and local_path_key(opkg["local_path"]) == new_key:
            raise Fail(f"{name}: local_path {new} is already registered by {other!r} — "
                       f"one registry row per checkout; refusing")
    now = dt.datetime.now(dt.timezone.utc)
    lease = pkg.get("lease")
    refuse_invalid_lease(name, lease)
    if lease_live(lease, now) and agent != lease.get("agent"):
        holder = lease.get("agent")
        raise Fail(f"{name}: lease held by {holder} until "
                   f"{lease_expiry(lease).isoformat(timespec='seconds')} — the holder "
                   f"relocates with --agent {holder} (--force is not accepted for --path)")

    new_abs = HOME / pathlib.Path(new)
    present = new_abs.is_dir()
    head = git_head(new_abs) if present else None
    if redetect and not present:
        raise Fail(f"{name}: cannot --redetect: path not present on this host: {new_abs}")
    notes: list[str] = []
    if not present:
        notes.append(f"path not present on this host: {new_abs} (other machines may hold it); "
                     f"commit-bound evidence cannot be confirmed here")
    elif head is None:
        notes.append(f"no git HEAD at {new_abs}; commit-bound evidence cannot be confirmed here")

    cleared: list[str] = []
    gate = copy.deepcopy(pkg.get("gate") or {})
    if gate.get("receipt"):
        kept, commit = _bound_at(new_abs, head, gate["receipt"])
        if not kept:
            cleared.append("gate.receipt")
            notes.append(f"gate.receipt cleared: {gate['receipt']} (commit {str(commit)[:12]}) "
                         f"is not bound to HEAD {str(head)[:12]} at the new path")
            gate["receipt"] = None
    if gate.get("comparator"):
        kept = bool(gate.get("receipt")) and _bound_at(new_abs, head, gate["comparator"])[0]
        if not kept:
            cleared.append("gate.comparator")
            notes.append(f"gate.comparator cleared: {gate['comparator']} is not bound to "
                         f"HEAD {str(head)[:12]} at the new path")
            gate["comparator"] = None
    csc = pkg.get("claims_synced_commit")
    if (csc or pkg.get("claims")) and not (csc and head and csc == head):
        cleared += ["claims_synced_commit", "claims"]
        notes.append(f"claims cache cleared (synced at {str(csc)[:12]}, HEAD {str(head)[:12]}); "
                     f"run `lean-pkg claims sync {name}`")

    old_status = pkg.get("status")
    new_status = old_status
    if "gate.receipt" in cleared:
        if old_status == "verified-private":
            new_status = "gate-pending"
            notes.append(f"status verified-private -> gate-pending: re-gate with "
                         f"`lean-pkg gate {name} --receipt R`")
        elif old_status in ("public", "upstreamed"):
            notes.append(f"status {old_status} unchanged (one-way); record fresh evidence "
                         f"with `lean-pkg gate {name} --receipt R`")

    redetected: dict[str, dict] = {}
    if redetect:
        tool, _ = detect_toolchain(new_abs)
        pin, _ = detect_mathlib_pin(new_abs)
        for field, value in (("lean_toolchain", tool), ("mathlib_pin", pin)):
            if pkg.get(field) != value:
                redetected[field] = {"from": pkg.get(field), "to": value}
    else:
        notes.append(f"lean_toolchain/mathlib_pin left as recorded; run "
                     f"`lean-pkg update {name} --redetect` to re-read toolchain/pin")

    payload = {"ok": True, "name": name, "event": "relocated", "old_path": old,
               "new_path": new, "cleared": cleared, "notes": notes, "dry_run": bool(dry_run),
               "status": ({"from": old_status, "to": new_status}
                          if new_status != old_status else None),
               "redetected": redetected}
    out = [f"{name}: relocated {old} -> {new}"]
    out += [f"  cleared: {c}" for c in cleared]
    out += [f"  {f}: {v['from']!r} -> {v['to']!r}" for f, v in redetected.items()]
    out += [f"  note: {n}" for n in notes]
    if dry_run:
        out.append("DRY RUN — nothing written. Re-run without --dry-run to relocate.")
        return out, payload

    trial = copy.deepcopy(reg)
    tp = trial["packages"][name]
    tp["local_path"], tp["gate"], tp["status"] = new, gate, new_status
    if "claims" in cleared:
        tp["claims_synced_commit"], tp["claims"] = None, []
    for field, v in redetected.items():
        tp[field] = v["to"]
    errors, preexisting = split_new_errors(validate_registry(reg), validate_registry(trial))
    if errors:
        raise Fail(f"{name}: relocation would break the registry schema:\n  "
                   + "\n  ".join(errors))
    report_preexisting(preexisting)
    for field in ("local_path", "gate", "status", "claims_synced_commit", "claims",
                  *redetected):
        if field in tp:
            pkg[field] = tp[field]
    fields = {"old_path": old, "new_path": new, "reason": reason, "cleared": cleared}
    if agent:
        fields["agent"] = agent
    if redetected:
        fields["redetected"] = redetected
    record(reg, events_p, pkg, "relocated", **fields)
    if new_status != old_status:
        record(reg, events_p, pkg, "status-changed", status_from=old_status,
               status_to=new_status, reason=f"relocated: {', '.join(cleared)} cleared")
    save_registry(reg_path, reg)
    return out, payload


# --------------------------------------------------------------------------
# gate

RL_REMINDER = "results-ledger: record the outcome for {name} in your results ledger"

def validate_receipt(pkg: dict, repo: pathlib.Path, receipt: dict) -> None:
    for f in RECEIPT_FIELDS:
        if f not in receipt:
            raise Fail(f"receipt missing required field {f!r}")
    commit = str(receipt["commit"])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Fail(f"receipt commit is not a full lowercase 40-hex sha: {commit!r}")
    head = git_head(repo)
    if head is None:
        raise Fail(f"cannot resolve HEAD in {repo}")
    ok, note = evidence_only_descendant(repo, commit, head)
    if not ok:
        raise Fail(f"receipt commit {commit[:12]}… != repo HEAD {head[:12]}… — "
                   f"the receipt must bind to HEAD or an evidence-only descendant "
                   f"({note})")
    check_toolchain_at(repo, commit, receipt["lean_toolchain"], "toolchain mismatch")
    if not str(receipt["mathlib_rev"]).strip():
        raise Fail("receipt mathlib_rev is empty")
    sc = receipt["sorry_count"]
    if isinstance(sc, bool) or not isinstance(sc, int):
        raise Fail(f"sorry_count must be an integer, got {sc!r}")
    if sc != 0:
        raise Fail(f"sorry_count must be 0, got {sc} — the trust gate is not clean")
    axioms = receipt["axioms"]
    if not isinstance(axioms, list) or not all(isinstance(a, str) for a in axioms):
        raise Fail("axioms must be a list of strings")
    allowed = set((pkg.get("gate") or {}).get("axioms_allowed") or DEFAULT_AXIOMS)
    extra = sorted(set(axioms) - allowed)
    if extra:
        raise Fail(f"axioms {extra} outside allowed set {sorted(allowed)}")
    sha = str(receipt["report_sha256"])
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        raise Fail(f"report_sha256 must be 64 hex chars, got {sha!r}")
    for f in ("job_id", "checker", "generated_at"):
        if not str(receipt[f]).strip():
            raise Fail(f"receipt field {f!r} is empty")
    check_timestamp(receipt["generated_at"], "receipt")
    receipt_job_slug(receipt["job_id"])


def check_timestamp(value, what: str) -> None:
    """generated_at must parse as an ISO-8601 timestamp (parse_ts), not merely be non-empty."""
    try:
        parse_ts(value)
    except (ValueError, TypeError):
        raise Fail(f"{what} field 'generated_at' is not an ISO-8601 timestamp: {value!r}") from None


def receipt_job_slug(job_id) -> str:
    """The job id as it appears in the stored receipt's file name: characters outside
    [A-Za-z0-9._-] become '-', and leading dots and dashes are stripped (so '../../evil' is
    'evil', never a '..'-led name). An empty result is refused."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(job_id)).lstrip(".-")
    if not slug:
        raise Fail(f"receipt job_id {str(job_id)!r} leaves no usable file-name characters")
    return slug


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git_show_bytes(repo: pathlib.Path, rel: str, rev: str = "HEAD") -> bytes | None:
    """Bytes of <rev>:<rel>, or None when the path is not tracked there."""
    p = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{rel}"],
                       capture_output=True)
    return p.stdout if p.returncode == 0 else None


def tracked_at(repo: pathlib.Path, rel: str, rev: str = "HEAD") -> bool:
    """True when <rel> (relative to the package directory `repo`) is committed at <rev>."""
    return git(repo, "cat-file", "-e", f"{rev}:./{rel}").returncode == 0


def check_toolchain_at(repo: pathlib.Path, commit: str, claimed, label: str) -> None:
    """The receipt's lean_toolchain must equal the lean-toolchain file committed at the bound
    commit (relative to the package directory). The working-tree file never counts."""
    blob = git_show_bytes(repo, "./lean-toolchain", commit)
    if blob is None:
        raise Fail(f"{repo}: no lean-toolchain committed at {commit[:12]}")
    committed = blob.decode("utf-8", "replace").strip()
    if str(claimed).strip() != committed:
        wt = repo / "lean-toolchain"
        work = wt.read_text().strip() if wt.is_file() else None
        raise Fail(f"{label}: receipt={str(claimed).strip()!r} committed at {commit[:12]}="
                   f"{committed!r}"
                   + (f" (the working-tree file says {work!r}; uncommitted files do not count)"
                      if work is not None and work != committed else ""))


def dirty_tracked(repo: pathlib.Path) -> list[str]:
    """Tracked files under the package directory that differ from HEAD (staged or not)."""
    p = git(repo, "status", "--porcelain", "--untracked-files=no", "--", ".")
    return [x[3:] for x in p.stdout.splitlines() if x.strip()]


def dirty_guard(repo: pathlib.Path, name: str, refuse: bool) -> list[str]:
    """Evidence is judged at the bound commit, never the working tree; a dirty checkout is a WARN
    on stderr (the lab's workflow edits while gating) or, with --refuse-dirty, a refusal."""
    dirty = dirty_tracked(repo)
    if dirty:
        shown = ", ".join(dirty[:5]) + (", ..." if len(dirty) > 5 else "")
        msg = (f"{name}: {len(dirty)} tracked file(s) modified in the checkout ({shown}); "
               f"receipts are checked against the bound commit, not these edits")
        if refuse:
            raise Fail(f"--refuse-dirty: {msg}; commit or stash them first")
        print(f"WARN: {msg} (--refuse-dirty refuses)", file=sys.stderr)
    return dirty


COMPARATOR_CONFIG_RE = re.compile(r"^(comparator[^/]*\.json|comparator/config[^/]*\.json)$")


def cfg_name(path: str) -> str:
    """Same rule as comparator-check.sh / comparator-receipt: strip .json, '/' -> '_'."""
    base = path[:-5] if path.endswith(".json") else path
    return base.replace("/", "_")


def head_comparator_configs(repo: pathlib.Path, rev: str = "HEAD") -> list[str]:
    """Every comparator configuration tracked at <rev> (the set the runner checks)."""
    p = git(repo, "ls-tree", "-r", "--name-only", rev)
    if p.returncode != 0:
        return []
    return sorted(x for x in p.stdout.splitlines() if COMPARATOR_CONFIG_RE.match(x))


def _safe_rel(path, what: str) -> str:
    """A repo-relative path: no leading '/', no '..' component."""
    if not isinstance(path, str) or not path.strip():
        raise Fail(f"comparator {what} path is empty")
    pp = pathlib.PurePosixPath(path)
    if pp.is_absolute() or ".." in pp.parts:
        raise Fail(f"comparator {what} path must be repo-relative without '..': {path!r}")
    return path


def validate_comparator(pkg: dict, repo: pathlib.Path, comp: dict,
                        receipt_commit: str | None) -> None:
    """Re-derive a comparator PASS from bytes (see DESIGN.md). Fails closed."""
    if not isinstance(comp, dict):
        raise Fail("comparator receipt must be a JSON object")
    for f in COMPARATOR_FIELDS:
        if f not in comp:
            raise Fail(f"comparator receipt missing required field {f!r}")
    if comp.get("test_only"):
        raise Fail("comparator receipt is marked test_only (produced under the test-only digest "
                   "override); it is not evidence")
    schema = comp["schema"]
    if schema not in COMPARATOR_SCHEMAS:
        raise Fail(f"comparator schema must be one of {COMPARATOR_SCHEMAS}, got {schema!r}")
    if schema == "comparator-receipt/1":
        mechanism = "legacy"
    else:
        mechanism = comp.get("mechanism")
        if mechanism not in COMPARATOR_MECHANISMS:
            raise Fail(f"comparator mechanism must be one of {COMPARATOR_MECHANISMS}, "
                       f"got {mechanism!r}")
    commit = str(comp["commit"])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Fail(f"comparator commit is not a full lowercase 40-hex sha: {commit!r}")
    head = git_head(repo)
    if head is None:
        raise Fail(f"cannot resolve HEAD in {repo}")
    ok, note = evidence_only_descendant(repo, commit, head)
    if not ok:
        raise Fail(f"comparator commit {commit[:12]}… != repo HEAD {head[:12]}… — "
                   f"the comparator receipt must bind to HEAD or an evidence-only "
                   f"descendant ({note})")
    if receipt_commit and commit != receipt_commit:
        raise Fail(f"comparator commit {commit[:12]}… != lower-tier receipt commit "
                   f"{receipt_commit[:12]}… — both tiers must bind to one commit")
    check_toolchain_at(repo, commit, comp["lean_toolchain"], "comparator toolchain mismatch")
    tools = comp["tools"]
    if not isinstance(tools, dict):
        raise Fail("comparator tools must be a mapping")
    if mechanism == "legacy":
        for tname in COMPARATOR_TOOLS:
            v = str(tools.get(tname) or "")
            if not re.fullmatch(r"[0-9a-f]{40}", v):
                raise Fail(f"comparator tools.{tname} must be a 40-hex commit, got {v!r}")
    else:
        if not str(tools.get("lean_toolchain") or "").strip():
            raise Fail("comparator tools.lean_toolchain is empty (toolchain mechanism)")
        for tname in COMPARATOR_TOOLCHAIN_DIGESTS:
            v = str(tools.get(tname) or "")
            if not re.fullmatch(r"[0-9a-f]{64}", v):
                raise Fail(f"comparator tools.{tname} must be a 64-hex sha256, got {v!r}")
    for f in ("host", "job_id", "generated_at"):
        if not str(comp[f]).strip():
            raise Fail(f"comparator field {f!r} is empty")
    check_timestamp(comp["generated_at"], "comparator receipt")
    if comp["result"] != "pass":
        raise Fail(f"comparator result must be 'pass', got {comp['result']!r}")
    configs = comp["configs"]
    if not isinstance(configs, list) or not configs:
        raise Fail("comparator configs must be a non-empty list")
    allowed = set((pkg.get("gate") or {}).get("axioms_allowed") or DEFAULT_AXIOMS)
    listed = [_safe_rel(c.get("path"), "config") for c in configs if isinstance(c, dict)]
    expected = head_comparator_configs(repo, commit)
    if sorted(listed) != expected:
        missing = sorted(set(expected) - set(listed))
        extra = sorted(set(listed) - set(expected))
        raise Fail(f"comparator receipt must list exactly the configs tracked at "
                   f"{commit[:12]} (bound commit; HEAD {head[:12]}): "
                   f"missing {missing}, extra/duplicate {extra}")
    for i, cfg in enumerate(configs):
        if not isinstance(cfg, dict):
            raise Fail(f"comparator configs[{i}] is not an object")
        for f in COMPARATOR_CONFIG_FIELDS:
            if f not in cfg:
                raise Fail(f"comparator configs[{i}] missing field {f!r}")
        path = _safe_rel(cfg["path"], "config")
        if cfg["result"] != "pass":
            raise Fail(f"comparator config {path} result must be 'pass', "
                       f"got {cfg['result']!r}")
        blob = git_show_bytes(repo, path, commit)
        if blob is None:
            raise Fail(f"comparator config {path} is not tracked at {commit[:12]} (HEAD {head[:12]})")
        if sha256_bytes(blob) != str(cfg["sha256"]).lower():
            raise Fail(f"comparator config {path} sha256 mismatch against bytes at {commit[:12]}")
        try:
            blob_cfg = json.loads(blob)
        except ValueError:
            blob_cfg = None
        if not isinstance(blob_cfg, dict):
            raise Fail(f"comparator config {path} at {commit[:12]} is not a JSON object")
        for f in ("challenge_module", "solution_module", "theorem_names",
                  "definition_names", "permitted_axioms"):
            if cfg[f] != blob_cfg.get(f):
                raise Fail(f"comparator config {path}: receipt {f} differs from the "
                           f"config at {commit[:12]} ({cfg[f]!r} vs {blob_cfg.get(f)!r})")
        names = cfg["theorem_names"]
        if not isinstance(names, list) or not names \
                or not all(isinstance(n, str) and n for n in names):
            raise Fail(f"comparator config {path}: theorem_names must be a "
                       f"non-empty list of strings")
        ax = cfg["permitted_axioms"]
        if not isinstance(ax, list) or not all(isinstance(a, str) for a in ax):
            raise Fail(f"comparator config {path}: permitted_axioms must be a "
                       f"list of strings")
        extra = sorted(set(ax) - allowed)
        if extra:
            raise Fail(f"comparator config {path}: permitted_axioms {extra} outside "
                       f"allowed set {sorted(allowed)}")
        # Definition holes (value-blind in comparator) need a human judged_as sign-off,
        # judged against the config bytes at the bound commit, never the receipt's copy.
        try:
            check_definition_holes(blob_cfg, f"comparator config {path}")
        except ValueError as e:
            raise Fail(str(e)) from None
        log_rel = _safe_rel(cfg["log"], "log")
        if not log_rel.startswith(".lean-receipts/"):
            raise Fail(f"comparator log must live under .lean-receipts/: {log_rel!r}")
        bound = f".lean-receipts/{commit[:12]}-comparator-{cfg_name(path)}.log"
        if log_rel != bound:
            raise Fail(f"comparator log for {path} must be {bound}, got {log_rel!r}")
        if (repo / ".lean-receipts").is_symlink():
            raise Fail(f"{repo}/.lean-receipts is a symlink; evidence must live inside the repo")
        log_p = repo / log_rel
        if log_p.is_symlink():
            raise Fail(f"comparator log {log_rel} is a symlink; evidence must be a regular file")
        if not log_p.is_file():
            raise Fail(f"comparator log missing: {log_rel}")
        log_bytes = log_p.read_bytes()
        if sha256_bytes(log_bytes) != str(cfg["log_sha256"]).lower():
            raise Fail(f"comparator log_sha256 mismatch for {log_rel}")
        lines = log_bytes.decode("utf-8", "replace").splitlines()
        if not comparator_pass("\n".join(lines), mechanism):
            raise Fail(f"comparator log {log_rel} has no PASS line for the {mechanism} mechanism "
                       f"({COMPARATOR_PASS_LINE!r} as a full line)")


def load_json_object(path_arg, what: str) -> tuple[pathlib.Path, dict]:
    p = pathlib.Path(str(path_arg)).expanduser()
    if not p.is_file():
        raise Fail(f"{what} not found: {p}")
    try:
        obj = json.loads(p.read_text())
    except ValueError as e:
        raise Fail(f"{what} is not valid JSON: {e}")
    if not isinstance(obj, dict):
        raise Fail(f"{what} must be a JSON object")
    return p, obj


def copy_comparator(repo: pathlib.Path, commit: str,
                    src: pathlib.Path) -> tuple[str, str]:
    dest_rel = f".lean-receipts/{commit[:12]}-comparator.json"
    dest = repo / dest_rel
    if dest.exists():
        if recorded_json(repo, dest_rel) != json.loads(src.read_text()):
            raise Fail(f"{dest} already holds a different comparator receipt for commit {commit[:12]}; "
                       f"to keep it, pass that file (--receipt {dest}); to record the newer one, move that "
                       f"file out of .lean-receipts/ yourself and re-run (a --replace that records "
                       f"comparator-replaced is planned)")
        return dest_rel, f"comparator receipt already present at {dest_rel} (not re-copied)"
    dest.parent.mkdir(exist_ok=True)
    shutil.copy2(src, dest)
    return dest_rel, (f"comparator receipt copied to {dest} — NOT committed there; "
                      f"commit it with its logs in the verified repo yourself")


def receipt_private_strings(blobs: list[tuple[str, bytes]], refuse: bool,
                            sink: list | None = None) -> list[dict]:
    """Scan receipt bytes (keys and values) for private strings before they are copied into the
    repository. Each hit is {"field", "kind"} (the private text itself is never echoed). With
    `refuse`, any hit is a refusal; otherwise the hits are returned (and appended to `sink`) and
    the caller prints them with warn_private once it knows what happened to the receipt."""
    from .index import private_string_hits
    hits = [{"field": f, "kind": k} for where, blob in blobs
            for f, k, _ in private_string_hits(blob, where)]
    if hits and refuse:
        raise Fail("--refuse-private: receipt holds private strings, nothing copied: "
                   + "; ".join(f"{h['field']} ({h['kind']})" for h in hits))
    if sink is not None:
        sink.extend(hits)
    return hits


# What happened to the receipt, by outcome event, for the private-string WARN line.
PRIVATE_WARN_OUTCOME = {
    "gate-passed": "the receipt is now in .lean-receipts/ as is",
    "comparator-attached": "the receipt is now in .lean-receipts/ as is",
    "already-gated": "already recorded; nothing copied now",
    "already-attached": "already recorded; nothing copied now",
    "comparator-checked": "nothing copied (--check)",
    None: "nothing copied (refused; reason below)",
}


def warn_private(hits: list[dict], event: str | None) -> None:
    outcome = PRIVATE_WARN_OUTCOME.get(event, PRIVATE_WARN_OUTCOME[None])
    for h in hits:
        print(f"WARN: {h['field']} looks like a {h['kind']}; {outcome} "
              f"(--refuse-private refuses such a receipt)", file=sys.stderr)


def _with_private_warnings(fn, *args, **kwargs) -> tuple[list[str], dict]:
    """Run do_gate/do_comparator's body and print the private-string WARN lines worded for
    the outcome: copied, already recorded, --check, or refused for another reason."""
    sink: list[dict] = []
    try:
        out, payload = fn(*args, private_sink=sink, **kwargs)
    except Fail:
        warn_private(sink, None)
        raise
    warn_private(sink, payload.get("event"))
    return out, payload


def recorded_json(repo: pathlib.Path, rel) -> dict | None:
    if not rel:
        return None
    try:
        return json.loads((repo / rel).read_text())
    except Exception:
        return None


def do_gate(reg_path, reg, events_p, name, receipt_arg,
            comparator_arg=None, refuse_private: bool = False,
            refuse_dirty: bool = False) -> tuple[list[str], dict]:
    return _with_private_warnings(_do_gate, reg_path, reg, events_p, name, receipt_arg,
                                  comparator_arg, refuse_private, refuse_dirty=refuse_dirty)


def _do_gate(reg_path, reg, events_p, name, receipt_arg, comparator_arg=None,
             refuse_private: bool = False, private_sink=None,
             refuse_dirty: bool = False) -> tuple[list[str], dict]:
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    dirty = dirty_guard(repo, name, refuse_dirty)
    rp, receipt = load_json_object(receipt_arg, "receipt")
    cp = comp = None
    if comparator_arg is not None:
        cp, comp = load_json_object(comparator_arg, "comparator receipt")
    private = receipt_private_strings([("receipt", rp.read_bytes())]
                                      + ([("comparator", cp.read_bytes())] if cp else []),
                                      refuse_private, private_sink)
    gate = pkg.get("gate") or {}
    # Idempotent retry: an identical receipt (pair) already recorded is a
    # no-op success, so agent retries are safe.
    recorded = recorded_json(repo, gate.get("receipt"))
    if recorded is not None and recorded == receipt:
        rec_comp = recorded_json(repo, gate.get("comparator"))
        if comp is None or (rec_comp is not None and rec_comp == comp):
            payload = {"ok": True, "name": name, "event": "already-gated",
                       "status": pkg.get("status"), "gate": pkg.get("gate"),
                       "receipt": gate.get("receipt"), "private_strings": private,
                       "dirty": dirty}
            return ([f"{name}: already gated (receipt {gate.get('receipt')}); "
                     f"nothing to do"], payload)
        raise Fail(f"{name}: already gated with this receipt; to add or replace "
                   f"comparator evidence use `lean-pkg comparator {name} --receipt ...`")
    # gate-pending is the designed entry. A package that was public before it was
    # ever gated (for example a collaborator-owned repository registered as public)
    # may also receive evidence: the status stays public, the receipt and the
    # gate-passed event are recorded. verified-private re-certifies through the
    # manual move back to gate-pending.
    if pkg.get("status") not in ("gate-pending", "public"):
        raise Fail(f"{name}: gate requires status gate-pending (or public, for "
                   f"evidence on an already-public package), got {pkg.get('status')!r}"
                   + (f"; to re-gate a new commit: `lean-pkg update {name} --status gate-pending "
                      f"--reason TEXT`, then `lean-pkg gate {name} --receipt R`"
                      if pkg.get("status") == "verified-private" else ""))
    was_public = pkg.get("status") == "public"
    validate_receipt(pkg, repo, receipt)
    commit = receipt["commit"]
    if comp is not None:
        validate_comparator(pkg, repo, comp, commit)

    job = receipt_job_slug(receipt["job_id"])
    dest_rel = f".lean-receipts/{commit[:12]}-{job}.json"
    dest = repo / dest_rel
    out: list[str] = []
    if dest.exists():
        if recorded_json(repo, dest_rel) != receipt:
            raise Fail(f"{dest} already holds a different receipt for commit {commit[:12]} and job "
                       f"{receipt['job_id']}; pass that file (--receipt {dest}), or give the new run its own "
                       f"job_id")
        out.append(f"receipt already present at {dest_rel} (not re-copied)")
    else:
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(rp, dest)
        out.append(f"receipt copied to {dest} — NOT committed there; "
                   f"commit it in the verified repo yourself")
    pkg.setdefault("gate", {})["receipt"] = dest_rel
    fields = dict(commit=commit, job_id=str(receipt["job_id"]),
                  checker=str(receipt["checker"]), receipt=dest_rel)
    comp_rel = None
    if comp is not None:
        comp_rel, note = copy_comparator(repo, commit, cp)
        out.append(note)
        pkg["gate"]["comparator"] = comp_rel
        fields["comparator"] = comp_rel
    else:
        prev_comp = pkg["gate"].get("comparator")
        prev = recorded_json(repo, prev_comp) if prev_comp else None
        if prev_comp and (prev is None or str(prev.get("commit")) != commit):
            pkg["gate"]["comparator"] = None
            fields["comparator_cleared"] = prev_comp
            out.append(f"comparator evidence {prev_comp} cleared: it was bound to another commit; "
                       f"attach a fresh one with `lean-pkg comparator {name} --receipt ...`")
        else:
            pkg["gate"].setdefault("comparator", None)
    if was_public:
        fields["status_unchanged"] = True
        new_status = "public"
    else:
        new_status = "verified-private"
    pkg["status"] = new_status
    record(reg, events_p, pkg, "gate-passed", **fields)
    save_registry(reg_path, reg)
    if was_public:
        out.append(f"{name}: public, evidence recorded, status unchanged (commit {commit[:12]})"
                   + (f" with comparator evidence {comp_rel}" if comp_rel else ""))
    else:
        out.append(f"{name}: gate-pending -> verified-private (commit {commit[:12]})"
                   + (f" with comparator evidence {comp_rel}" if comp_rel else ""))
    out.append(RL_REMINDER.format(name=name))
    payload = {"ok": True, "name": name, "event": "gate-passed",
               "status": new_status, "gate": pkg["gate"],
               "receipt": dest_rel, "commit": commit, "rl_reminder": True,
               "status_unchanged": was_public, "private_strings": private, "dirty": dirty}
    if comp_rel:
        payload["comparator"] = comp_rel
    return out, payload


def do_comparator(reg_path, reg, events_p, name, receipt_arg,
                  check_only: bool = False, refuse_private: bool = False,
                  refuse_dirty: bool = False) -> tuple[list[str], dict]:
    return _with_private_warnings(_do_comparator, reg_path, reg, events_p, name, receipt_arg,
                                  check_only, refuse_private, refuse_dirty=refuse_dirty)


def _do_comparator(reg_path, reg, events_p, name, receipt_arg, check_only: bool = False,
                   refuse_private: bool = False, private_sink=None,
                   refuse_dirty: bool = False) -> tuple[list[str], dict]:
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    dirty = dirty_guard(repo, name, refuse_dirty)
    cp, comp = load_json_object(receipt_arg, "comparator receipt")
    private = receipt_private_strings([("comparator", cp.read_bytes())], refuse_private, private_sink)
    gate = pkg.get("gate") or {}
    if check_only:
        lower = recorded_json(repo, gate.get("receipt"))
        lower_commit = str(lower.get("commit") or "") if lower else None
        validate_comparator(pkg, repo, comp, lower_commit or None)
        n = len(comp["configs"])
        msg = (f"{name}: comparator receipt valid at commit {comp['commit'][:12]} "
               f"({n} config(s)) — not recorded (--check)"
               + ("" if lower_commit else "; no lower-tier receipt recorded yet"))
        payload = {"ok": True, "name": name, "event": "comparator-checked",
                   "recorded": False, "commit": comp["commit"], "configs": n,
                   "lower_receipt_commit": lower_commit, "private_strings": private,
                   "dirty": dirty}
        return [msg], payload
    rec_comp = recorded_json(repo, gate.get("comparator"))
    if rec_comp is not None and rec_comp == comp:
        payload = {"ok": True, "name": name, "event": "already-attached",
                   "status": pkg.get("status"), "gate": gate,
                   "comparator": gate.get("comparator"), "private_strings": private,
                   "dirty": dirty}
        return ([f"{name}: comparator receipt already attached "
                 f"({gate.get('comparator')}); nothing to do"], payload)
    if pkg.get("status") not in ("verified-private", "public"):
        raise Fail(f"{name}: comparator requires status verified-private or public "
                   f"(gate first), got {pkg.get('status')!r}")
    lower = recorded_json(repo, gate.get("receipt"))
    if lower is None:
        raise Fail(f"{name}: no readable lower-tier receipt at {gate.get('receipt')!r}; "
                   f"run `lean-pkg gate` first")
    validate_comparator(pkg, repo, comp, str(lower.get("commit") or ""))
    commit = comp["commit"]
    comp_rel, note = copy_comparator(repo, commit, cp)
    pkg.setdefault("gate", {})["comparator"] = comp_rel
    record(reg, events_p, pkg, "comparator-attached", commit=commit,
           comparator=comp_rel, host=str(comp["host"]), job_id=str(comp["job_id"]))
    save_registry(reg_path, reg)
    out = [note, f"{name}: comparator-attached ({comp_rel}, commit {commit[:12]}, "
                 f"{len(comp['configs'])} config(s))"]
    payload = {"ok": True, "name": name, "event": "comparator-attached",
               "status": pkg.get("status"), "gate": pkg["gate"],
               "comparator": comp_rel, "commit": commit, "private_strings": private,
               "dirty": dirty}
    return out, payload


# --------------------------------------------------------------------------
# register — the ONLY write path for new packages (agents never hand-edit yaml)

def do_register(reg_path, reg, events_p, name, repo, local_path, owner_project,
                visibility, status, upstream_target, automation, notes,
                workspace, agent, dry_run, reason: str = "") -> tuple[list[str], dict]:
    if status not in REGISTER_STATUSES:
        raise Fail(f"register cannot set status {status!r} (allowed: "
                   f"{', '.join(REGISTER_STATUSES)}); verified-private is reached through "
                   f"`lean-pkg gate`, public through `lean-pkg promote --yes`")
    if status == "public":
        if visibility != "public":
            raise UsageError("register --status public requires --visibility public")
        if not str(reason or "").strip():
            raise UsageError("register --status public requires --reason (why an already-public "
                             "repository is registered without promote)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise Fail(f"bad package name {name!r} — use kebab-case-ish "
                   f"[A-Za-z0-9._-]+ not starting with a punctuation mark")
    pkgs = reg["packages"]
    if name in pkgs:
        raise Fail(f"name already registered: {name!r} — refusing to "
                   f"overwrite; inspect with `lean-pkg status {name}`")
    require_nonempty("--repo", repo)
    require_nonempty("--owner-project", owner_project)
    local_path, new_key = normalize_local_path(local_path)
    for existing, pkg in pkgs.items():
        if pkg.get("local_path") and local_path_key(pkg["local_path"]) == new_key:
            raise Fail(f"local_path already registered by {existing!r}: "
                       f"{pkg.get('local_path')} — one registry row per "
                       f"checkout; refusing (hand-editing missed exactly this)")
    repo_abs = HOME / pathlib.Path(local_path)
    warnings: list[str] = []
    toolchain = mathlib_pin = None
    if not repo_abs.is_dir():
        warnings.append(f"local_path does not exist on this host: {repo_abs} — "
                        f"lean_toolchain and mathlib_pin set to null")
    else:
        toolchain, w = detect_toolchain(repo_abs)
        if w:
            warnings.append(w)
        mathlib_pin, w = detect_mathlib_pin(repo_abs)
        if w:
            warnings.append(w)
    notes_out = namespace_notes(namespace_clashes(reg, repo_abs)) if repo_abs.is_dir() else []
    entry = {
        "name": name, "repo": repo, "local_path": local_path,
        "visibility": visibility, "status": status,
        "owner_project": owner_project, "workspace": workspace, "agent": agent,
        "lease": None, "lean_toolchain": toolchain, "mathlib_pin": mathlib_pin,
        "upstream_target": upstream_target, "automation": automation or "",
        "gate": {"receipt": None, "comparator": None,
                 "axioms_allowed": list(DEFAULT_AXIOMS)},
        "promoted_at": None, "promoted_commit": None, "reaudit_after": None,
        "notes": notes or "", "history": [],
        **copy.deepcopy(V2_PACKAGE_DEFAULTS),
        **copy.deepcopy(V3_PACKAGE_DEFAULTS),
    }
    payload = {"name": name, "dry_run": dry_run, "package": entry,
               "warnings": warnings, "notes": notes_out}
    if dry_run:
        lines = [f"warn: {w}" for w in warnings]
        lines.append(yaml.safe_dump({name: entry}, sort_keys=False,
                                    default_flow_style=False, width=120).rstrip())
        lines.append("DRY RUN — nothing written. Re-run without --dry-run "
                     "to register.")
        return lines, payload
    before = validate_registry(reg)
    pkgs[name] = entry
    # safety net: never write a schema error this registration introduces; pre-existing
    # errors on other packages are reported on stderr and do not block
    errors, preexisting = split_new_errors(before, validate_registry(reg))
    if errors:
        del pkgs[name]
        raise Fail("register would produce an invalid registry: "
                   + "; ".join(errors))
    report_preexisting(preexisting)
    if preexisting:
        payload["preexisting_errors"] = preexisting
    record(reg, events_p, entry, "registered", repo=repo,
           local_path=local_path, status=status, visibility=visibility,
           lean_toolchain=toolchain, mathlib_pin=mathlib_pin,
           workspace=workspace, agent=agent,
           **({"reason": reason} if str(reason or "").strip() else {}))
    save_registry(reg_path, reg)
    payload["event"] = "registered"
    lines = [f"warn: {w}" for w in warnings]
    lines.append(f"{name}: registered (status={status}, "
                 f"visibility={visibility}, toolchain={toolchain or '?'}, "
                 f"pin={mathlib_pin or '?'})")
    return lines, payload


# --------------------------------------------------------------------------
# promote


def do_init(reg_path: pathlib.Path, events_p: pathlib.Path, force: bool) -> tuple[list[str], dict]:
    """Bootstrap an empty registry and its event log. Refuses to touch a registry that holds packages."""
    existed = reg_path.exists()
    if existed:
        try:
            existing = yaml.safe_load(reg_path.read_text()) or {}
        except yaml.YAMLError:
            existing = None
        if not force:
            raise Fail(f"registry already exists: {reg_path} (use --force only to "
                       f"re-create an EMPTY registry)")
        if not isinstance(existing, dict) or (existing.get("packages") or {}):
            raise Fail(f"refusing --force: {reg_path} holds packages or is not a valid YAML registry")
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": REGISTRY_VERSION, "packages": {}}
    save_registry(reg_path, data)
    # The shared event log records the registry file NAME only: an absolute path is one machine's.
    append_event(events_p, now_iso(), None, "registry-created", registry=reg_path.name,
                 version=REGISTRY_VERSION, **({"forced": True} if force and existed else {}))
    out = [f"registry created: {reg_path} (version {REGISTRY_VERSION}); "
           f"events: {events_p}"]
    return out, {"ok": True, "event": "registry-created", "registry": str(reg_path),
                 "events": str(events_p), "version": REGISTRY_VERSION, "forced": bool(force and existed)}


def migrate_registry(data: dict) -> tuple[dict, list[str]]:
    """Pure: return (migrated copy, change log). v1 -> v2 -> v3 is additive."""
    out = copy.deepcopy(data)
    changes: list[str] = []
    v = out.get("version")
    if v not in SUPPORTED_REGISTRY_VERSIONS:
        raise Fail(f"cannot migrate registry version {v!r}; supported: {SUPPORTED_REGISTRY_VERSIONS}")
    if v != REGISTRY_VERSION:
        out["version"] = REGISTRY_VERSION
        changes.append(f"version: {v} -> {REGISTRY_VERSION}")
    for name, pkg in (out.get("packages") or {}).items():
        for k, default in {**V2_PACKAGE_DEFAULTS, **V3_PACKAGE_DEFAULTS}.items():
            if k not in pkg:
                pkg[k] = copy.deepcopy(default)
                changes.append(f"{name}: + {k}")
    return out, changes


def do_migrate(reg_path, events_p, dry_run: bool) -> tuple[list[str], dict]:
    data = load_registry(reg_path)
    from_v = data.get("version")
    new, changes = migrate_registry(data)
    payload = {"ok": True, "dry_run": dry_run, "from_version": from_v,
               "to_version": new["version"], "changes": changes}
    if not changes:
        return [f"registry already at version {new['version']}; nothing to do"], payload
    lines = [f"  {c}" for c in changes]
    if dry_run:
        lines.append("DRY RUN \u2014 nothing written.")
        return lines, payload
    save_registry(reg_path, new)
    append_event(events_p, now_iso(), None, "registry-migrated",
                 from_version=from_v, to_version=new["version"], changes=len(changes))
    lines.append(f"migrated {reg_path}: version {from_v} -> {new['version']} ({len(changes)} change(s))")
    payload["event"] = "registry-migrated"
    return lines, payload


def _palomar_entry(pkg: dict, commit: str) -> dict:
    pal = pkg.setdefault("palomar", [])
    for e in pal:
        if e.get("commit") == commit:
            return e
    e = {"commit": commit, "id": None, "version": None, "comparator_config": None,
         "registered_at": None, "review_outcome": None, "preflight_run": None}
    pal.append(e)
    return e


def do_retract(reg_path, reg, events_p, name, fields: dict, reason: str) -> tuple[list[str], dict]:
    """record NAME retract --field event=E --field ts=T --reason R: mark an evidence entry retracted.

    Append-only: the entry stays in history[]; an evidence-retracted entry names it, and the validator
    and the upstream-evidence check ignore what it names."""
    missing = [f for f in ("event", "ts") if not str(fields.get(f, "")).strip()]
    if missing:
        raise Fail(f"retract requires --field for: {', '.join(missing)} (the event name and ts of the "
                   f"history entry to retract; see `lean-pkg status {name} --json`)")
    unknown = sorted(set(fields) - {"event", "ts"})
    if unknown:
        raise Fail(f"retract: unknown --field key(s) {', '.join(unknown)}; allowed: event, ts")
    if not (reason or "").strip():
        raise Fail("retract requires --reason (why the evidence entry is wrong)")
    target, ts = str(fields["event"]).strip(), str(fields["ts"]).strip()
    if target not in EVIDENCE_EVENTS:
        raise Fail(f"retract: {target!r} is not an evidence event; one of {sorted(EVIDENCE_EVENTS)}")
    pkg = get_pkg(reg, name)
    hits = [h for h in pkg.get("history") or []
            if isinstance(h, dict) and h.get("event") == target and str(h.get("ts")) == ts]
    if not hits:
        known = [str(h.get("ts")) for h in pkg.get("history") or []
                 if isinstance(h, dict) and h.get("event") == target]
        raise Fail(f"{name}: no {target} history entry at ts {ts} (recorded at: {', '.join(known) or 'none'})")
    gone = {_fingerprint(e) for e in retracted_entries(pkg)}
    hits = [h for h in hits if _fingerprint(h) not in gone]
    if not hits:
        raise Fail(f"{name}: {target} at {ts} is already retracted")
    if len(hits) > 1:
        raise Fail(f"{name}: {len(hits)} {target} entries share ts {ts}; retract cannot tell them apart")
    entry = {"retracted_event": target, "retracted_ts": ts, "retracted_entry": copy.deepcopy(hits[0]),
             "reason": reason}
    trial = copy.deepcopy(reg)
    trial["packages"][name].setdefault("history", []).append(
        {"ts": now_iso(), "event": "evidence-retracted", **entry})
    errors, preexisting = split_new_errors(validate_registry(reg), validate_registry(trial))
    if errors:
        raise Fail(f"{name}: retract would break the registry schema:\n  " + "\n  ".join(errors))
    report_preexisting(preexisting)
    record(reg, events_p, pkg, "evidence-retracted", **entry)
    save_registry(reg_path, reg)
    payload = {"ok": True, "name": name, "event": "evidence-retracted",
               "retracted": {"event": target, "ts": ts}, "reason": reason}
    if preexisting:
        payload["preexisting_errors"] = preexisting
    return [f"{name}: {target} at {ts} retracted"], payload


def _rule_fail(rule: str, msg: str) -> Fail:
    """A record refusal that names the evidence rule it enforces."""
    return Fail(f"{msg} [rule {rule}]")


def _int_or_none(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _evidence_version(v) -> int:
    n = _int_or_none(v)
    if n is None:
        raise _rule_fail("palomar-version", f"version must be an integer, got {v!r}")
    return n


def check_evidence_fields(pkg: dict, event: str, fields: dict) -> list[str]:
    """Shape and binding checks on an evidence event's --field values, before any effect.
    Refuses (naming the rule) or returns warning notes, which are also printed on stderr."""
    notes: list[str] = []
    if event == "doi-minted":
        for k in ("doi", "concept"):
            if k in fields and str(fields[k]).strip() and not DOI_RE.fullmatch(str(fields[k]).strip()):
                raise _rule_fail("doi-format", f"{k} must be a DOI (10.<4-9 digits>/<suffix>), "
                                 f"got {fields[k]!r}")
    elif event in ("mathlib-pr-opened", "mathlib-pr-merged"):
        if not GITHUB_URL_RE.fullmatch(str(fields["url"]).strip()):
            raise _rule_fail("mathlib-pr-url", f"url must be an https://github.com/<owner>/<repo>/... "
                             f"pull request URL, got {fields['url']!r}")
    elif event == "reservoir-listed":
        url = str(fields["url"]).strip().casefold()
        nwo = gh_nwo(pkg.get("repo") or "")
        wanted = [str(pkg.get("name") or "")] + ([nwo, nwo.split("/")[1]] if nwo else [])
        if not any(w and w.casefold() in url for w in wanted):
            notes.append(f"WARN: reservoir url names neither the package {pkg.get('name')!r} nor its "
                         f"repository {nwo or '(no GitHub repo)'}; check it is this package's page")
    elif event == "palomar-registered":
        status = pkg.get("status")
        if status not in PALOMAR_REGISTERED_STATUSES:
            raise _rule_fail("palomar-registered-public",
                             f"{pkg.get('name')}: palomar-registered needs status public or upstreamed "
                             f"(Palomar registers public code), got {status!r}")
        rel = str(fields["comparator_config"]).strip()
        repo = repo_path(pkg) if pkg.get("local_path") else None
        if repo is None or not repo.is_dir():
            raise _rule_fail("palomar-config-tracked",
                             f"{pkg.get('name')}: local_path does not exist; cannot check that "
                             f"comparator_config {rel} is tracked at HEAD")
        if os.path.isabs(rel) or git(repo, "cat-file", "-e", f"HEAD:./{rel}").returncode != 0:
            raise _rule_fail("palomar-config-tracked",
                             f"{pkg.get('name')}: comparator_config {rel} is not tracked at the package's "
                             f"HEAD; commit it (a repo-relative path) before recording palomar-registered")
    for n in notes:
        print(f"lean-pkg: {n}", file=sys.stderr)
    return notes


def warn_private_evidence(hits: list[dict]) -> None:
    for h in hits:
        print(f"WARN: {h['field']} looks like a {h['kind']}: {PRIVATE_EVIDENCE_HINT}", file=sys.stderr)


def do_record(reg_path, reg, events_p, name, event, fields: dict, reason: str) -> tuple[list[str], dict]:
    """Record a D5 evidence event: required fields checked, registry effect applied, the would-be registry
    validated (a schema error this event introduces refuses; nothing is written), event appended."""
    if event == RETRACT_EVENT:
        return do_retract(reg_path, reg, events_p, name, fields, reason)
    if event not in EVIDENCE_EVENTS:
        raise Fail(f"unknown evidence event {event!r}; one of {sorted(EVIDENCE_EVENTS)} "
                   f"(or {RETRACT_EVENT})")
    missing = [f for f in EVIDENCE_EVENTS[event] if not str(fields.get(f, "")).strip()]
    if missing:
        raise Fail(f"{event} requires --field for: {', '.join(missing)}")
    allowed = set(EVIDENCE_EVENTS[event]) | ({"concept"} if event == "doi-minted" else set())
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise Fail(f"{event}: unknown --field key(s) {', '.join(unknown)}; allowed: "
                   f"{', '.join(sorted(allowed))} (name, event, ts and reason are reserved; "
                   f"use --reason for free text)")
    if "commit" in fields and not _HEX40.fullmatch(str(fields["commit"])):
        raise Fail(f"commit must be a 40-hex sha, got {fields['commit']!r}")
    current = get_pkg(reg, name)
    notes = check_evidence_fields(current, event, fields)
    from .index import private_string_hits
    private = [{"field": f, "kind": k} for f, k, _ in
               private_string_hits(json.dumps(fields).encode(), "field")]
    if event == "palomar-preflight-pass" and any(
            isinstance(e, dict) and e.get("commit") == fields["commit"]
            and e.get("preflight_run") == fields["run"] for e in current.get("palomar") or []):
        warn_private_evidence(private)
        payload = {"ok": True, "name": name, "event": event, "fields": fields, "notes": notes,
                   "already_recorded": True, "private_strings": private}
        return [f"{name}: {event} already recorded for commit {fields['commit'][:12]} "
                f"(run {fields['run']}); nothing to do", *notes], payload
    before = validate_registry(reg)
    original = reg
    reg = copy.deepcopy(reg)                      # effects go to a copy until the result validates
    pkg = reg["packages"][name]
    for k, d in V2_PACKAGE_DEFAULTS.items():      # tolerate a v1 registry
        pkg.setdefault(k, copy.deepcopy(d))
    ts = now_iso()
    extra_out: list[str] = list(notes)
    if event == "consent-recorded":
        pkg["consent"].append({"who": fields["who"], "role": fields["role"],
                               "pointer": fields["pointer"], "recorded_at": ts})
    elif event in ("palomar-preflight-pass", "palomar-submitted", "palomar-review", "palomar-registered"):
        e = _palomar_entry(pkg, fields["commit"])
        if event == "palomar-preflight-pass":
            e["preflight_run"] = fields["run"]
        elif event == "palomar-submitted":
            e["submission_id"] = fields["submission_id"]
            e["comparator_config"] = fields["comparator_config"]
        elif event == "palomar-review":
            if fields["outcome"] not in ("neutral", "revision_required", "rejected"):
                raise Fail("outcome must be neutral | revision_required | rejected")
            e["review_outcome"] = fields["outcome"]
        else:
            e["version"] = _evidence_version(fields["version"])
            e["id"] = fields["id"]
            e["comparator_config"] = fields["comparator_config"]
            e["registered_at"] = ts
    elif event == "palomar-withdrawn":
        version = _evidence_version(fields["version"])
        for e in pkg["palomar"]:
            if e.get("id") == fields["id"] and _int_or_none(e.get("version")) == version:
                e["withdrawn_at"] = ts
                break
        else:
            raise Fail(f"no palomar entry {fields['id']} v{fields['version']} to withdraw")
    elif event == "doi-minted":
        d = pkg.get("doi") or {"concept": None, "versions": []}
        if fields.get("concept"):
            d["concept"] = fields["concept"]
        d["versions"].append({"doi": fields["doi"], "tag": fields["tag"],
                              "commit": fields["commit"], "minted_at": ts})
        pkg["doi"] = d
    elif event == "reservoir-listed":
        m = RESERVOIR_URL_RE.match(str(fields["url"]).strip())
        if not m:
            raise Fail("url must be the package's Reservoir page, "
                       "https://reservoir.lean-lang.org/@<owner>/<name>")
        owner, repo_name = m.group(1), m.group(2)
        status = reservoir_listing_status(owner, repo_name)
        if status == 404:
            raise Fail(f"{owner}/{repo_name} is not listed on Reservoir (API 404); listing is "
                       f"automatic for a public, non-fork GitHub repository with a root "
                       f"lake-manifest.json, a detected OSI license and at least 2 stars")
        verified = status == 200
        pkg["reservoir"] = {"url": fields["url"], "listed_at": ts, "verified": verified,
                            "verified_at": ts if verified else None}
        if not verified:
            extra_out.append(f"reservoir listing unverified (API status {status!r}; network "
                             f"unreachable or unexpected); re-run `record` later to verify")
    trial = copy.deepcopy(reg)
    trial["packages"][name].setdefault("history", []).append(
        {"ts": ts, "event": event, "reason": reason or "", **fields})
    errors, preexisting = split_new_errors(before, validate_registry(trial))
    if errors:
        raise Fail(f"{name}: {event} would break the registry schema; nothing written:\n  "
                   + "\n  ".join(errors))
    report_preexisting(preexisting)
    original["packages"][name] = pkg
    record(original, events_p, pkg, event, reason=reason or "", **fields)
    save_registry(reg_path, original)
    warn_private_evidence(private)
    payload = {"ok": True, "name": name, "event": event, "fields": fields, "notes": extra_out,
               "already_recorded": False, "private_strings": private}
    if preexisting:
        payload["preexisting_errors"] = preexisting
    return [f"{name}: {event} recorded", *extra_out], payload

def evidence_tracked_item(repo: pathlib.Path, what: str, rels: list[str]) -> tuple[str, str]:
    """✓ when every evidence path is committed at HEAD with the bytes the working tree holds;
    ✗ naming the untracked or modified ones (untracked files do not count, as in `index add`)."""
    untracked = [r for r in rels if not tracked_at(repo, r)]
    changed = [r for r in rels if r not in untracked and (repo / r).is_file()
               and git_show_bytes(repo, f"./{r}") != (repo / r).read_bytes()]
    if not untracked and not changed:
        return ("✓", f"{what} tracked at HEAD ({len(rels)} file(s))")
    bad = ([f"not committed: {', '.join(untracked[:3])}"] if untracked else []) \
        + ([f"modified since HEAD: {', '.join(changed[:3])}"] if changed else [])
    return ("✗", f"{what} not committed at HEAD — {'; '.join(bad)} (untracked files do not "
                 f"count; commit .lean-receipts/ first)")


def head_pushed_item(repo: pathlib.Path) -> tuple[str, str]:
    """HEAD must be contained in origin/<branch>, judged on local refs only (no fetch)."""
    head = git_head(repo)
    branch = current_branch(repo)
    if branch == "detached HEAD":
        branch = reference_branch(repo)[0] or "main"
    ref = f"refs/remotes/origin/{branch}"
    if head is None or _rev(repo, ref) is None:
        return ("✗", f"no origin/{branch} in local refs — push the branch first "
                     f"(local refs; nothing fetched)")
    if _is_ancestor(repo, head, ref):
        return ("✓", f"HEAD {head[:12]} contained in origin/{branch} (local refs; nothing fetched)")
    return ("✗", f"HEAD {head[:12]} not contained in origin/{branch} (local refs; nothing "
                 f"fetched) — push first; the public repository must hold the evidence")


def promote_checklist(pkg: dict, repo: pathlib.Path, yes: bool = False) -> list[tuple[str, str]]:
    """(mark, label) pairs; mark in {'✓', '✗', 'WARN'}. With yes (promote --yes), a failed gh
    visibility query is ✗, not WARN."""
    checks: list[tuple[str, str]] = []
    reuse = find_reuse()
    if reuse is None:
        checks.append(("WARN", "reuse not on PATH — REUSE license lint skipped"))
    else:
        p = run([reuse, "lint"], cwd=str(repo))
        if p.returncode == 0:
            checks.append(("✓", "reuse lint passed"))
        else:
            detail = (p.stderr or p.stdout).strip().splitlines()
            checks.append(("✗", f"reuse lint failed: {(detail or [''])[0][:120]}"))
    if any((repo / f).is_file() for f in LICENSE_NAMES):
        checks.append(("✓", "LICENSE/COPYING present at repo root"))
    else:
        checks.append(("✗", "no LICENSE/COPYING at repo root "
                            "(Reservoir requires a GitHub-detected OSI license)"))
    p = git(repo, "cat-file", "-e", "HEAD:lake-manifest.json")
    if p.returncode == 0:
        checks.append(("✓", "lake-manifest.json tracked at HEAD"))
    else:
        checks.append(("✗", "lake-manifest.json NOT tracked at HEAD "
                            "(Reservoir requires a root manifest)"))
    receipt_rel = (pkg.get("gate") or {}).get("receipt")
    head = git_head(repo)
    rcommit = None
    if not receipt_rel:
        checks.append(("✗", "no gate receipt recorded — run `lean-pkg gate` first"))
    else:
        try:
            rcommit = json.loads((repo / receipt_rel).read_text()).get("commit")
        except Exception:
            rcommit = None
        ok, note = (evidence_only_descendant(repo, rcommit, head)
                    if rcommit and head else (False, "no receipt commit"))
        if ok:
            checks.append(("✓", f"gate receipt bound to HEAD {head[:12]}"
                                + (f" ({note})" if note else "")))
        else:
            checks.append(("✗", f"gate receipt/HEAD drift: "
                                f"receipt={str(rcommit)[:12]} HEAD={str(head)[:12]} ({note})"))
        checks.append(evidence_tracked_item(repo, f"gate receipt {receipt_rel}", [receipt_rel]))
    comp_rel = (pkg.get("gate") or {}).get("comparator")
    if not comp_rel:
        checks.append(("✗", "no comparator receipt recorded — a comparator PASS "
                            "is required before public (`lean-pkg comparator`)"))
    else:
        comp = recorded_json(repo, comp_rel)
        if comp is None:
            checks.append(("✗", f"comparator receipt unreadable: {comp_rel}"))
        else:
            try:
                validate_comparator(pkg, repo, comp, rcommit if rcommit else None)
                checks.append(("✓", f"comparator PASS bound to HEAD {str(head)[:12]} "
                                    f"({len(comp['configs'])} config(s): config and log bytes "
                                    f"match the receipt, PASS line present; not re-run)"))
            except Fail as e:
                checks.append(("✗", f"comparator receipt invalid: {str(e)[:140]}", str(e)))
            logs = [c["log"] for c in comp.get("configs") or []
                    if isinstance(c, dict) and isinstance(c.get("log"), str) and c["log"]]
            checks.append(evidence_tracked_item(repo, f"comparator receipt {comp_rel} and its "
                                                      f"{len(logs)} log(s)", [comp_rel, *logs]))
    paper = pkg.get("paper")
    if paper_blocks_promote(pkg):
        checks.append(("✗", "paper is unpublished and no arXiv id is recorded: going public would disclose "
                            "its results before the preprint (`lean-pkg update "
                            f"{pkg.get('name')} --paper-arxiv ID` once it is on arXiv)"))
    elif isinstance(paper, dict):
        checks.append(("✓", f"paper {paper.get('status')}"
                            + (f", arXiv {paper['arxiv']}" if paper.get("arxiv") else "")))
    checks.append(head_pushed_item(repo))
    nwo = gh_nwo(pkg.get("repo") or "") or str(pkg.get("repo"))
    p = gh("repo", "view", nwo, "--json", "visibility", "-q", ".visibility")
    if p.returncode != 0:
        checks.append(("✗" if yes else "WARN", "gh visibility query failed: "
                       + ((p.stderr or p.stdout).strip().splitlines() or [""])[0]
                       + (" (--yes needs a working gh)" if yes else "")))
    else:
        gvis = p.stdout.strip().lower()
        if gvis == str(pkg.get("visibility")).lower():
            checks.append(("✓", f"gh visibility matches registry ({gvis})"))
        else:
            checks.append(("✗", f"visibility mismatch: "
                                f"registry={pkg.get('visibility')} gh={gvis}"))
    if pkg.get("upstream_target") == "mathlib":
        if str(pkg.get("automation") or "").strip():
            checks.append(("✓", "automation AI-disclosure note present "
                                "(required for mathlib target)"))
        else:
            checks.append(("✗", "upstream_target=mathlib requires a nonempty "
                                "automation AI-disclosure note"))
    else:
        checks.append(("✓", f"automation disclosure not required "
                            f"(upstream_target={pkg.get('upstream_target')})"))
    return checks


ONEWAY_WARNING = ("WARNING: a GitHub private→public flip is effectively ONE-WAY — "
                  "the code and its full history become permanently public; "
                  "flipping back does not unpublish forks, clones, or caches.")


_MARK_TO_STATUS = {"✓": "pass", "✗": "fail", "WARN": "warn"}


def do_promote(reg_path, reg, events_p, name, yes) -> tuple[list[str], int, dict]:
    pkg = get_pkg(reg, name)
    if pkg.get("status") != "verified-private":
        raise Fail(f"{name}: promote requires status verified-private, "
                   f"got {pkg.get('status')!r}")
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    lines = [f"promotion checklist for {name} ({repo}):"]
    checks = promote_checklist(pkg, repo, yes=yes)
    lines += [f"  {c[0]}  {c[1]}" for c in checks]
    payload = {"name": name, "dry_run": not yes,
               "checklist": [dict({"status": _MARK_TO_STATUS[c[0]], "label": c[1]},
                                  **({"reason": c[2]} if len(c) > 2 else {}))
                             for c in checks],
               "one_way_warning": ONEWAY_WARNING}
    if any(c[0] == "✗" for c in checks):
        lines.append("refusing: checklist has failures")
        payload.update(ok=False, refused=True)
        return lines, 1, payload
    lines.append(ONEWAY_WARNING)
    if not yes:
        lines.append("DRY RUN — nothing changed. Re-run with --yes to execute.")
        payload.update(ok=True, refused=False)
        return lines, 0, payload
    nwo = gh_nwo(pkg.get("repo") or "") or str(pkg.get("repo"))
    p = gh("repo", "edit", nwo, "--visibility", "public",
           "--accept-visibility-change-consequences")
    if p.returncode != 0:
        raise Fail(f"gh repo edit failed: {(p.stderr or p.stdout).strip()}")
    head = git_head(repo)
    pkg["visibility"] = "public"
    pkg["status"] = "public"
    pkg["promoted_at"] = now_iso()
    pkg["promoted_commit"] = head
    record(reg, events_p, pkg, "promoted", commit=head, visibility="public")
    save_registry(reg_path, reg)
    lines.append(f"{name}: verified-private -> public at {(head or '?')[:12]}")
    sent = notify(f"[lean-registry] {name} promoted to public: "
                  f"commit {(head or '')[:7]}")
    notified = sent == "sent"
    lines.append({"sent": "notified via LEAN_PKG_NOTIFY_CMD",
                  "failed": "notify command failed; notification skipped",
                  }.get(sent, "notify command not configured; notification skipped"))
    lines.append(RL_REMINDER.format(name=name))
    payload.update(ok=True, refused=False, event="promoted",
                   status="public", visibility="public",
                   promoted_at=pkg["promoted_at"], promoted_commit=head,
                   notified=notified, rl_reminder=True)
    return lines, 0, payload


# --------------------------------------------------------------------------
# audit

TOOLCHAIN_FLOOR_ENV = "LEAN_PKG_TOOLCHAIN_FLOOR"
DEFAULT_TOOLCHAIN_FLOOR = "leanprover/lean4:v4.35.0-rc2"   # Palomar's floor (lake comparator)
FLOOR_EXEMPT_STATUSES = ("scratch", "archived")
_TOOLCHAIN_RE = re.compile(r"^(?:leanprover/lean4:)?v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")


def parse_lean_toolchain(text) -> tuple[int, int, int, int, int] | None:
    """Sort key for a Lean toolchain string `[leanprover/lean4:]vMAJOR.MINOR.PATCH[-rcN]`,
    or None (unparsed: nightlies, channel names, anything else are never compared).
    An rc sorts before the release of the same version: (M, m, p, 0, N) < (M, m, p, 1, 0)."""
    m = _TOOLCHAIN_RE.match(str(text or "").strip())
    if not m:
        return None
    major, minor, patch, rc = m.groups()
    return (int(major), int(minor), int(patch)) + ((0, int(rc)) if rc is not None else (1, 0))


def resolve_toolchain_floor(flag: str | None) -> str:
    # an explicit flag, even an empty one, is used as given; an empty env variable counts as unset
    floor = flag if flag is not None else (os.environ.get(TOOLCHAIN_FLOOR_ENV, "").strip()
                                           or DEFAULT_TOOLCHAIN_FLOOR)
    if parse_lean_toolchain(floor) is None:
        raise UsageError(f"toolchain floor {floor!r} is not [leanprover/lean4:]vMAJOR.MINOR.PATCH[-rcN]")
    return floor


def render_finding(f: dict, width: int = 20) -> str:
    return f"{(f['check'] + ' ').ljust(width)}{f['name']}: {f['detail']}"


NO_GITHUB_MIRROR_DETAIL = "repo is not on GitHub; visibility not mirrored"


def render_findings(findings: list[dict]) -> list[str]:
    """Human audit output: no-github-mirror findings collapse into one summary line."""
    mirror = [f["name"] for f in findings if f["check"] == "no-github-mirror"]
    shown = [f for f in findings if f["check"] != "no-github-mirror"]
    width = max([20] + [len(f["check"]) + 2 for f in shown])
    lines = [render_finding(f, width) for f in shown]
    if mirror:
        lines.append(f"{len(mirror)} package(s) without a GitHub mirror (visibility not checked): "
                     + ", ".join(mirror))
    return lines


def status_visibility_mismatch(pkg: dict) -> str | None:
    """Cross-field rule, audited (warn) rather than validated: verified-private means a private
    repository, public/upstreamed a public one. Registries written before the rule may hold a
    mismatch, so `validate` does not refuse it."""
    status, vis = pkg.get("status"), pkg.get("visibility")
    if status == "verified-private" and vis != "private":
        return f"status verified-private but visibility {vis} (expected private)"
    if status in ("public", "upstreamed") and vis != "public":
        return f"status {status} but visibility {vis} (expected public)"
    return None


def audit_registry(reg_path, reg, events_p, fix: bool = False,
                   toolchain_floor: str | None = None) -> list[dict]:
    """Structured findings: {"check", "name", "detail", "fixed"}. Read-only unless fix:
    lease sweeps and visibility/archive reconciliation both need it (an expired lease is
    reported with fixed=False otherwise).
    toolchain_floor None: the environment's floor, else the default (the TUI path)."""
    findings: list[dict] = []
    if toolchain_floor is None:
        try:
            toolchain_floor = resolve_toolchain_floor(None)
        except UsageError:
            toolchain_floor = DEFAULT_TOOLCHAIN_FLOOR
    floor_key = parse_lean_toolchain(toolchain_floor)
    now = dt.datetime.now(dt.timezone.utc)
    mutated = False

    def note(check, name, detail, fixed=False):
        findings.append({"check": check, "name": name,
                         "detail": detail, "fixed": fixed})

    # Parse every lease before mutating anything: a lease that cannot be read is a finding, not a
    # traceback halfway through a --fix.
    bad_lease: set[str] = set()
    for name in sorted(reg["packages"]):
        lease = (reg["packages"][name] or {}).get("lease")
        if lease:
            try:
                lease_expiry(lease)
            except (TypeError, ValueError, KeyError, AttributeError) as e:
                bad_lease.add(name)
                note("lease-invalid", name,
                     f"lease cannot be parsed ({type(e).__name__}: {e}); not swept")

    by_path: dict[str, list[str]] = collections.defaultdict(list)
    for name in sorted(reg["packages"]):
        lpath = (reg["packages"][name] or {}).get("local_path")
        if lpath:
            by_path[local_path_key(lpath)].append(name)
    for names in by_path.values():
        for name in names if len(names) > 1 else ():
            # no path echo: local paths are private, and findings are printed
            note("duplicate-path", name, "local_path resolves to the same checkout as "
                 + ", ".join(n for n in names if n != name) + "; one checkout per package")

    for name in sorted(reg["packages"]):   # informational (#22.7): never fixed
        pkg = reg["packages"][name] or {}
        try:
            repo = repo_path(pkg) if pkg.get("local_path") else None
        except (OSError, ValueError, TypeError):
            repo = None
        if repo is None or not repo.is_dir():
            continue
        for ns, others in namespace_clashes(reg, repo, skip=name).items():
            note("duplicate-namespace", name, f"Lean root namespace {ns} is also used by "
                 + ", ".join(others) + "; the two cannot be built or imported together")

    def untracked(name, repo, tip, rel, label) -> None:
        if tip and git(repo, "cat-file", "-e", f"{tip}:./{rel}").returncode != 0:
            note("receipt-untracked", name, f"{rel} exists in the working tree but is not tracked at "
                 f"{label} {tip[:12]}; commit it so the evidence travels with the repository")

    for name in sorted(reg["packages"]):
        pkg = reg["packages"][name]
        mismatch = status_visibility_mismatch(pkg)
        if mismatch:
            note("status-visibility-mismatch", name, mismatch)
        lease = pkg.get("lease")
        if lease and name not in bad_lease and not lease_live(lease, now):
            who = f"{lease.get('agent')}@{lease.get('workspace')} until {lease_expiry(lease).isoformat()}"
            if fix:
                note("lease-expired", name, f"was {who} — cleared", fixed=True)
                record(reg, events_p, pkg, "lease-expired", agent=lease.get("agent"),
                       workspace=lease.get("workspace"),
                       claimed_at=lease.get("claimed_at"),
                       ttl_hours=lease.get("ttl_hours"))
                pkg["lease"] = None
                pkg["workspace"] = None
                pkg["agent"] = None
                mutated = True
            else:
                note("lease-expired", name, f"held by {who} — expired; `audit --fix` clears it")
        if pkg.get("upstream_target") == "palomar" and pkg.get("status") in ("verified-private", "public") \
                and not any(e.get("preflight_run") for e in (pkg.get("palomar") or []) if isinstance(e, dict)):
            note("palomar-preflight-missing", name,
                 "upstream_target=palomar but no palomar-preflight-pass recorded "
                 "(lean-pkg record NAME palomar-preflight-pass --field commit=… --field run=…)")
        tc_reg = str(pkg.get("lean_toolchain") or "").strip()
        if tc_reg and floor_key and pkg.get("status") not in FLOOR_EXEMPT_STATUSES:
            key = parse_lean_toolchain(tc_reg)
            if key is None:
                note("toolchain-unparsed", name,
                     f"{tc_reg} is not a release or rc toolchain; not compared with the floor {toolchain_floor}")
            elif key < floor_key:
                note("below-toolchain-floor", name, f"{tc_reg} is older than the floor {toolchain_floor}")
        repo = repo_path(pkg) if pkg.get("local_path") else None
        if repo is None or not repo.is_dir():
            note("missing-path", name, str(repo))
            continue
        tc = repo / "lean-toolchain"
        if tc.is_file():
            actual = tc.read_text().strip()
            if actual != str(pkg.get("lean_toolchain") or "").strip():
                note("toolchain-drift", name,
                     f"registry={pkg.get('lean_toolchain')} file={actual}")
        else:
            note("toolchain-missing", name, str(tc))
        gate_rec = pkg.get("gate") or {}
        ref_branch = ref_tip = None
        off_ref = ""
        if gate_rec.get("receipt") or gate_rec.get("comparator"):
            ref_branch, ref_tip, ref_note = reference_branch(repo)
            here = current_branch(repo)
            at_tip = here == "detached HEAD" and git_head(repo) == ref_tip
            if ref_branch and here != ref_branch and not at_tip:
                off_ref = f"checkout on {here}; judged against {ref_branch}"
            if ref_note:
                off_ref = f"{off_ref}; {ref_note}" if off_ref else ref_note
            if off_ref:
                note("checkout-branch", name, off_ref)
        ref_label = ref_branch or "HEAD"
        suffix = f" ({off_ref})" if off_ref else ""
        receipt_rel = gate_rec.get("receipt")
        if receipt_rel:
            rp = repo / receipt_rel
            if not rp.is_file():
                note("receipt-missing", name, str(receipt_rel))
            else:
                untracked(name, repo, ref_tip, receipt_rel, ref_label)
                try:
                    rcommit = json.loads(rp.read_text()).get("commit")
                except Exception:
                    rcommit = None
                if rcommit and ref_tip and not evidence_only_descendant(repo, rcommit, ref_tip)[0]:
                    note("receipt-drift", name,
                         f"receipt={str(rcommit)[:12]} {ref_label}={ref_tip[:12]}{suffix}")
        comp_rel = gate_rec.get("comparator")
        if comp_rel:
            cpth = repo / comp_rel
            if not cpth.is_file():
                note("comparator-missing", name, str(comp_rel))
            else:
                untracked(name, repo, ref_tip, comp_rel, ref_label)
                ccommit = (recorded_json(repo, comp_rel) or {}).get("commit")
                if ccommit and ref_tip and not evidence_only_descendant(repo, ccommit, ref_tip)[0]:
                    note("comparator-drift", name,
                         f"comparator={str(ccommit)[:12]} {ref_label}={ref_tip[:12]}{suffix}")
        if pkg.get("formalization_yaml"):
            # formalization-missing | formalization-drift | warn: the written
            # formalization.yaml must equal what the generator emits today.
            from . import formalization as formalization_mod
            for check, detail in formalization_mod.audit_findings(pkg, repo):
                note(check, name, detail)
        nwo = gh_nwo(pkg.get("repo") or "")
        if not nwo:
            # no path echo: the repo may be a private local path, and findings are printed
            note("no-github-mirror", name, NO_GITHUB_MIRROR_DETAIL)
            continue
        p = gh("repo", "view", nwo, "--json", "visibility,isArchived")
        if p.returncode != 0:
            note("warn", name,
                 f"gh query failed: {(p.stderr or p.stdout).strip()[:120]}")
            continue
        try:
            info = json.loads(p.stdout)
        except ValueError:
            note("warn", name, "gh returned non-JSON")
            continue
        gvis = str(info.get("visibility") or "").lower()
        if gvis and gvis != str(pkg.get("visibility")).lower():
            note("visibility-mismatch", name,
                 f"registry={pkg.get('visibility')} gh={info.get('visibility')}"
                 + (" — fixed" if fix else ""), fixed=fix)
            if fix:
                record(reg, events_p, pkg, "audit-fix", field="visibility",
                       old=pkg.get("visibility"), new=gvis, source="gh")
                pkg["visibility"] = gvis
                mutated = True
        if info.get("isArchived") and pkg.get("status") != "archived":
            note("archived-upstream", name,
                 f"gh isArchived=true, registry status={pkg.get('status')}"
                 + (" — fixed" if fix else ""), fixed=fix)
            if fix:
                record(reg, events_p, pkg, "audit-fix", field="status",
                       old=pkg.get("status"), new="archived", source="gh")
                pkg["status"] = "archived"
                mutated = True
    if mutated:
        save_registry(reg_path, reg)
    return findings


# --------------------------------------------------------------------------
# validate

def validate_registry(data) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["top level is not a mapping"]
    version = data.get("version")
    if version not in SUPPORTED_REGISTRY_VERSIONS:
        errors.append(f"version must be one of {SUPPORTED_REGISTRY_VERSIONS}, got {version!r}")
    pkgs = data.get("packages")
    if not isinstance(pkgs, dict):
        errors.append("packages must be a mapping")
        return errors
    for key, pkg in pkgs.items():
        pfx = f"packages.{key}"
        if not isinstance(pkg, dict):
            errors.append(f"{pfx}: not a mapping")
            continue
        for f in REQUIRED_FIELDS:
            if f not in pkg:
                errors.append(f"{pfx}: missing field {f!r}")
        if pkg.get("name") != key:
            errors.append(f"{pfx}: name field {pkg.get('name')!r} != map key {key!r}")
        if version in (2, 3):
            for f in V2_PACKAGE_DEFAULTS:
                if f not in pkg:
                    errors.append(f"{pfx}: missing v2 field {f!r} (run `lean-pkg migrate`)")
        if version == 3:
            for f in V3_PACKAGE_DEFAULTS:
                if f not in pkg:
                    errors.append(f"{pfx}: missing v3 field {f!r} (run `lean-pkg migrate`)")
        if version == 3 or any(f in pkg for f in V3_PACKAGE_DEFAULTS):
            if "claims" in pkg and not isinstance(pkg["claims"], list):
                errors.append(f"{pfx}.claims: must be a list")
            cf = pkg.get("claims_file", "claims.yaml")
            if not isinstance(cf, str) or not cf.strip():
                errors.append(f"{pfx}.claims_file: must be a non-empty repo-relative path")
            csc = pkg.get("claims_synced_commit")
            if csc is not None and not _HEX40.fullmatch(str(csc)):
                errors.append(f"{pfx}.claims_synced_commit: must be 40 lowercase hex or null")
        if version in (2, 3) or any(f in pkg for f in V2_PACKAGE_DEFAULTS):
            for f in ("palomar", "consent"):
                if f in pkg and not isinstance(pkg[f], list):
                    errors.append(f"{pfx}.{f}: must be a list")
            for f in ("doi", "reservoir"):
                if f in pkg and pkg[f] is not None and not isinstance(pkg[f], dict):
                    errors.append(f"{pfx}.{f}: must be a mapping or null")
            for f in ("license_spdx", "formalization_yaml"):
                if f in pkg and pkg[f] is not None and not isinstance(pkg[f], str):
                    errors.append(f"{pfx}.{f}: must be a string or null")
            doi = pkg.get("doi")
            if isinstance(doi, dict):
                pal_commits = [e.get("commit") for e in (pkg.get("palomar") or []) if isinstance(e, dict)]
                retracted = {(str(h.get("doi")), str(h.get("commit")), str(h.get("tag")))   # ignored versions
                             for h in retracted_entries(pkg) if h.get("event") == "doi-minted"}
                for v in doi.get("versions") or []:
                    c = str((v or {}).get("commit") or "")
                    if isinstance(v, dict) and (str(v.get("doi")), c, str(v.get("tag"))) in retracted:
                        continue
                    if c and c not in pal_commits:
                        known = ", ".join(str(x)[:12] for x in pal_commits) or "none"
                        errors.append(f"{pfx}: doi {v.get('doi')} commit {c[:12]} matches no "
                                      f"palomar entry (palomar commits: {known})")
        errors += paper_errors(pkg.get("paper"), pfx)
        if pkg.get("status") not in STATUSES:
            errors.append(f"{pfx}: bad status {pkg.get('status')!r} "
                          f"(allowed: {', '.join(STATUSES)})")
        if pkg.get("visibility") not in VISIBILITIES:
            errors.append(f"{pfx}: bad visibility {pkg.get('visibility')!r}")
        if pkg.get("upstream_target") not in UPSTREAM_TARGETS:
            errors.append(f"{pfx}: bad upstream_target "
                          f"{pkg.get('upstream_target')!r}")
        lease = pkg.get("lease")
        if lease is not None:
            if not isinstance(lease, dict):
                errors.append(f"{pfx}.lease: not a mapping")
            else:
                for f in LEASE_FIELDS:
                    if f not in lease:
                        errors.append(f"{pfx}.lease: missing field {f!r}")
                if "claimed_at" in lease:
                    try:
                        parse_ts(lease["claimed_at"])
                    except Exception:
                        errors.append(f"{pfx}.lease: unparseable claimed_at "
                                      f"{lease.get('claimed_at')!r}")
                if not isinstance(lease.get("ttl_hours"), int) \
                        or lease.get("ttl_hours", 0) <= 0:
                    errors.append(f"{pfx}.lease: ttl_hours must be a positive int")
        gate = pkg.get("gate")
        if not isinstance(gate, dict):
            errors.append(f"{pfx}.gate: not a mapping")
        else:
            if "receipt" not in gate:
                errors.append(f"{pfx}.gate: missing key 'receipt'")
            ax = gate.get("axioms_allowed")
            if not isinstance(ax, list) or not all(isinstance(a, str) for a in ax):
                errors.append(f"{pfx}.gate: axioms_allowed must be a list of strings")
            comp = gate.get("comparator")
            if comp is not None and not isinstance(comp, str):
                errors.append(f"{pfx}.gate: comparator must be a string path or null")
            if pkg.get("status") == "verified-private" and not gate.get("receipt"):
                errors.append(f"{pfx}: status verified-private requires gate.receipt "
                              f"(only `lean-pkg gate` reaches verified-private)")
        hist = pkg.get("history")
        if not isinstance(hist, list):
            errors.append(f"{pfx}.history: not a list")
        else:
            for i, e in enumerate(hist):
                if not isinstance(e, dict) or not isinstance(e.get("ts"), str) \
                        or not isinstance(e.get("event"), str):
                    errors.append(f"{pfx}.history[{i}]: needs string ts + event")
                    continue
                try:
                    parse_ts(e["ts"])
                except Exception:
                    errors.append(f"{pfx}.history[{i}]: unparseable ts {e['ts']!r}")
        ra = pkg.get("reaudit_after")
        if ra is not None:
            try:
                dt.date.fromisoformat(str(ra))
            except ValueError:
                errors.append(f"{pfx}: reaudit_after is not an ISO date: {ra!r}")
    return errors


# --------------------------------------------------------------------------
# board / list / status

def row_fields(pkg: dict, now: dt.datetime) -> tuple[str, str, str, str, str, str, str]:
    lease = pkg.get("lease")
    if lease and lease_invalid(lease):
        label = "? INVALID"   # fields unparseable: `release NAME --force --reason` clears it
    elif lease:
        label = f"{lease.get('agent')}@{lease.get('workspace') or '-'}"
        if not lease_live(lease, now):
            label += " EXPIRED"
    else:
        label = "-"
    return (str(pkg.get("name")), str(pkg.get("status")),
            str(pkg.get("visibility")), label, lease_expires_label(lease, now),
            str(pkg.get("mathlib_pin") or "-"),
            str(pkg.get("reaudit_after") or "-"))


def table_lines(reg: dict, now: dt.datetime | None = None) -> list[str]:
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = [row_fields(reg["packages"][k], now) for k in sorted(reg["packages"])]
    widths = [max([len(BOARD_HEADER[i])] + [len(r[i]) for r in rows])
              for i in range(len(BOARD_HEADER))]
    fmt = lambda r: "  ".join(str(r[i]).ljust(widths[i]) for i in range(len(BOARD_HEADER))).rstrip()
    return [fmt(BOARD_HEADER)] + [fmt(r) for r in rows]


def cmd_board(args) -> int:
    reg_path = registry_path(args)
    if args.json_out:
        print_json(with_expiry(load_registry(reg_path)))
        return 0
    if board_interactive():
        try:
            import curses
        except ImportError:
            pass
        else:
            _Tui(curses, reg_path, events_path(args), commit_ctx=getattr(args, "commit_ctx", None)).start()
            return 0
    print("\n".join(table_lines(load_registry(reg_path))))
    return 0


def cmd_list(args) -> int:
    reg = load_registry(registry_path(args))
    if args.json_out:
        print_json(with_expiry(reg))
        return 0
    print("\n".join(table_lines(reg)))
    return 0


def cmd_status(args) -> int:
    reg = load_registry(registry_path(args))
    pkg = get_pkg(reg, args.name)
    hist = pkg.get("history") or []
    if args.json_out:
        print_json({"ok": True, "name": args.name, "package": pkg,
                    "history_count": len(hist)})
        return 0
    view = dict(pkg)
    view["history"] = f"<{len(hist)} entries; last 5 below>"
    print(yaml.safe_dump({args.name: view}, sort_keys=False,
                         default_flow_style=False, width=120).rstrip())
    print(f"history (last {min(5, len(hist))} of {len(hist)}):")
    for e in hist[-5:]:
        print("  " + json.dumps(e, sort_keys=True))
    return 0


# --------------------------------------------------------------------------
# TUI

def parse_ttl_hours(text: str) -> int:
    """The TUI's TTL prompt: blank -> DEFAULT_TTL_HOURS; otherwise a whole number of hours > 0, else Fail
    (never a silent default: "1.5" or "0" is refused, not turned into 24)."""
    t = (text or "").strip()
    if not t:
        return DEFAULT_TTL_HOURS
    try:
        ttl = int(t)
    except ValueError:
        raise Fail(f"ttl must be a whole number of hours, got {t!r}")
    if ttl <= 0:
        raise Fail(f"ttl must be a positive number of hours, got {t!r}")
    return ttl


TUI_HELP = """\
lean-pkg board — keys

  j / DOWN   move selection down
  k / UP     move selection up
  c          claim package (CAS lease; prompts workspace/agent/ttl/reason)
  r          release lease (holder agent; blank agent = --force)
  g          gate: validate a lower-tier receipt (gate-pending -> verified-private)
  p          promote checklist (DRY-RUN ONLY here; execute via CLI --yes)
  a          audit (read-only: reports expired leases, gh visibility, drift)
  e          show the last event-log entry for the selected package
  ?          this help
  q / ESC    quit

Colors: green=public/upstreamed  yellow=active/gate-pending
        magenta=verified-private  cyan=scratch  dim=archived  red=expired lease
"""


class _Tui:
    STATUS_PAIRS = {"public": 1, "upstreamed": 1, "active": 2, "gate-pending": 2,
                    "scratch": 4, "verified-private": 5}

    def __init__(self, curses, reg_path, events_p, commit_ctx=None):
        self.curses = curses
        self.reg_path = reg_path
        self.events_p = events_p
        self.commit_ctx = commit_ctx   # committed mode: commit after each successful write
        self.sel = 0
        self.msg = ""
        self.has_color = False

    def start(self):
        ensure_interactive()  # refuse (exit 2) rather than block on non-tty input
        self.curses.wrapper(self._main)

    def reload(self):
        self.reg = load_registry(self.reg_path)
        self.names = sorted(self.reg["packages"])
        self.sel = min(self.sel, max(0, len(self.names) - 1))

    def _main(self, stdscr):
        c = self.curses
        self.stdscr = stdscr
        try:
            c.curs_set(0)
        except c.error:
            pass
        try:
            c.start_color()
            c.use_default_colors()
            for i, color in enumerate([c.COLOR_GREEN, c.COLOR_YELLOW, c.COLOR_RED,
                                       c.COLOR_CYAN, c.COLOR_MAGENTA], start=1):
                c.init_pair(i, color, -1)
            self.has_color = True
        except c.error:
            self.has_color = False
        self.reload()
        while True:
            self.draw()
            k = stdscr.getch()
            if k in (ord("q"), 27):
                break
            elif k in (ord("j"), c.KEY_DOWN):
                self.sel = min(self.sel + 1, len(self.names) - 1)
            elif k in (ord("k"), c.KEY_UP):
                self.sel = max(self.sel - 1, 0)
            elif k == ord("c"):
                self.key_claim()
            elif k == ord("r"):
                self.key_release()
            elif k == ord("g"):
                self.key_gate()
            elif k == ord("p"):
                self.key_promote()
            elif k == ord("a"):
                self.key_audit()
            elif k == ord("e"):
                self.key_event()
            elif k == ord("?"):
                self.show_text("lean-pkg board — help", TUI_HELP)

    def attr_for(self, pkg, now):
        c = self.curses
        lease = pkg.get("lease")
        if lease and not lease_live_or_none(lease, now):
            return c.color_pair(3) if self.has_color else c.A_BOLD
        if pkg.get("status") == "archived":
            return c.A_DIM
        pair = self.STATUS_PAIRS.get(pkg.get("status"))
        return c.color_pair(pair) if (pair and self.has_color) else 0

    def draw(self):
        c = self.curses
        h, w = self.stdscr.getmaxyx()
        self.stdscr.erase()
        now = dt.datetime.now(dt.timezone.utc)
        self.stdscr.addnstr(0, 0, f"lean-pkg board — {self.reg_path}", w - 1, c.A_BOLD)
        rows = [(n, row_fields(self.reg["packages"][n], now)) for n in self.names]
        widths = [max([len(BOARD_HEADER[i])] + [len(r[1][i]) for r in rows])
                  for i in range(len(BOARD_HEADER))]
        fmt = lambda r: "  ".join(str(r[i]).ljust(widths[i])
                                  for i in range(len(BOARD_HEADER)))
        self.stdscr.addnstr(2, 0, fmt(BOARD_HEADER), w - 1, c.A_UNDERLINE | c.A_BOLD)
        for i, (name, r) in enumerate(rows[: max(0, h - 5)]):
            attr = self.attr_for(self.reg["packages"][name], now)
            if i == self.sel:
                attr |= c.A_REVERSE
            self.stdscr.addnstr(3 + i, 0, fmt(r), w - 1, attr)
        if self.msg:
            self.stdscr.addnstr(h - 2, 0, self.msg, w - 1, c.A_BOLD)
        self.stdscr.addnstr(h - 1, 0,
                            "c claim · r release · g gate · p promote(dry) · "
                            "a audit · e event · ? help · q quit", w - 1, c.A_DIM)
        self.stdscr.refresh()

    def prompt(self, label: str) -> str:
        h, w = self.stdscr.getmaxyx()
        self.stdscr.addnstr(h - 1, 0, " " * (w - 1), w - 1)
        self.stdscr.addnstr(h - 1, 0, label, w - 1)
        self.stdscr.refresh()
        self.curses.echo()
        try:
            s = self.stdscr.getstr(h - 1, min(len(label), w - 2), 200)
        finally:
            self.curses.noecho()
        return s.decode(errors="replace").strip()

    def confirm(self, label: str) -> bool:
        return self.prompt(label + " [y/N] ").lower() == "y"

    def show_text(self, title: str, text: str):
        c = self.curses
        lines = text.splitlines() or [""]
        off = 0
        while True:
            h, w = self.stdscr.getmaxyx()
            body = max(1, h - 2)
            self.stdscr.erase()
            self.stdscr.addnstr(0, 0, title, w - 1, c.A_BOLD)
            for i, line in enumerate(lines[off:off + body]):
                self.stdscr.addnstr(i + 1, 0, line, w - 1)
            self.stdscr.addnstr(h - 1, 0, "j/k scroll · q close", w - 1, c.A_DIM)
            self.stdscr.refresh()
            k = self.stdscr.getch()
            if k in (ord("q"), 27):
                break
            if k in (ord("j"), c.KEY_DOWN):
                off = min(off + 1, max(0, len(lines) - body))
            elif k in (ord("k"), c.KEY_UP):
                off = max(0, off - 1)

    def committed(self, msg: str) -> str:
        """In committed mode, commit the write just made (the caller holds the registry lock)."""
        if self.commit_ctx is None:
            return msg
        note = commit_now(self.commit_ctx)
        return f"{msg} · {note}" if note else msg

    def selected(self) -> str | None:
        return self.names[self.sel] if self.names else None

    def key_claim(self):
        name = self.selected()
        if not name:
            return
        ws = self.prompt(f"claim {name} — workspace: ")
        if not ws:
            self.msg = "claim cancelled"
            return
        agent = self.prompt(f"claim {name} — agent: ")
        if not agent:
            self.msg = "claim cancelled"
            return
        try:
            ttl = parse_ttl_hours(self.prompt(f"ttl hours [{DEFAULT_TTL_HOURS}]: "))
        except Fail as e:
            self.msg = f"claim cancelled: {e}"
            return
        reason = self.prompt("reason (optional): ")
        if not self.confirm(f"claim {name} for {agent}@{ws} ({ttl}h)?"):
            self.msg = "claim cancelled"
            return
        try:
            with registry_lock(self.reg_path):   # act on the registry as it is now, not as last drawn
                self.reload()
                out, _ = do_claim(self.reg_path, self.reg, self.events_p,
                                  name, ws, agent, ttl, reason, False)
                self.msg = self.committed(out[-1])
        except Fail as e:
            self.msg = f"refused: {e}"
        self.reload()

    def key_release(self):
        name = self.selected()
        if not name:
            return
        agent = self.prompt(f"release {name} — holder agent (blank = FORCE): ")
        force = agent == ""
        reason = ""
        if force:
            reason = self.prompt("reason (required for FORCE): ")
            if not reason:
                self.msg = "release cancelled: FORCE needs a reason"
                return
        if not self.confirm(f"release {name}{' (FORCE)' if force else ''}?"):
            self.msg = "release cancelled"
            return
        try:
            with registry_lock(self.reg_path):
                self.reload()
                out, _ = do_release(self.reg_path, self.reg, self.events_p,
                                    name, agent or None, force, reason)
                self.msg = self.committed(out[-1])
        except Fail as e:
            self.msg = f"refused: {e}"
        self.reload()

    def key_gate(self):
        name = self.selected()
        if not name:
            return
        path = self.prompt(f"gate {name} — receipt path: ")
        if not path:
            self.msg = "gate cancelled"
            return
        if not self.confirm(f"run gate on {name} with {path}?"):
            self.msg = "gate cancelled"
            return
        try:
            with registry_lock(self.reg_path):
                self.reload()
                out, _ = do_gate(self.reg_path, self.reg, self.events_p, name, path)
                self.msg = self.committed(out[-1])
            self.show_text(f"gate {name}", "\n".join(out))
        except Fail as e:
            self.msg = f"gate refused: {e}"
        self.reload()

    def key_promote(self):
        name = self.selected()
        if not name:
            return
        try:
            out, _rc, _ = do_promote(self.reg_path, self.reg, self.events_p,
                                     name, yes=False)
            self.show_text(f"promote (dry-run) {name}", "\n".join(out))
        except Fail as e:
            self.msg = f"promote refused: {e}"
        self.reload()

    def key_audit(self):
        if not self.confirm("run audit? (read-only here: expired leases are reported, not cleared)"):
            self.msg = "audit cancelled"
            return
        try:
            findings = audit_registry(self.reg_path, self.reg, self.events_p,
                                      fix=False)
            text = "\n".join(render_findings(findings)) or "no findings"
            self.show_text("audit", text)
            self.msg = f"audit: {len(findings)} finding(s)"
        except Fail as e:
            self.msg = f"audit failed: {e}"
        self.reload()

    def key_event(self):
        name = self.selected()
        if not name:
            return
        last = None
        if self.events_p.is_file():
            for line in self.events_p.read_text().splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("name") == name:
                    last = e
        self.show_text(f"last event — {name}",
                       json.dumps(last, indent=2, sort_keys=True)
                       if last else "no events for this package")


# --------------------------------------------------------------------------
# wiki — render a Markdown status page (e.g. for a static wiki)

WIKI_STATUS_TIERS = ["active", "gate-pending", "verified-private", "scratch",
                     "public", "upstreamed", "archived"]

WIKI_BANNER = ("machine-generated by `lean-pkg wiki` from the lean-pkg "
               "registry — do not hand-edit; regenerate with `lean-pkg wiki`.")


def load_candidates(path: pathlib.Path) -> dict | None:
    if not path or not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return None
    if isinstance(data, dict) and isinstance(data.get("candidates"), dict):
        return data
    return None


def render_wiki(reg: dict, candidates: dict | None,
                today: str | None = None, now: dt.datetime | None = None) -> str:
    today = today or utc_today()
    now = now or dt.datetime.now(dt.timezone.utc)

    def sort_key(item):
        name, pkg = item
        try:
            tier = WIKI_STATUS_TIERS.index(pkg.get("status"))
        except ValueError:
            tier = len(WIKI_STATUS_TIERS)
        return (tier, name)

    L = ["---", "title: Lean Verification Status", "type: lean-verification",
         f"updated: '{today}'", "---", "",
         f"> {WIKI_BANNER}", "",
         "## Packages", "",
         "| name | status | visibility | lease | mathlib pin | reaudit_after |",
         "|---|---|---|---|---|---|"]
    for name, pkg in sorted(reg["packages"].items(), key=sort_key):
        lease = pkg.get("lease")
        label = ("? (invalid lease)" if lease_invalid(lease) else
                 f"{lease.get('agent')}@{lease.get('workspace') or '-'}" if lease else "-")
        L.append(f"| {name} | {pkg.get('status')} | {pkg.get('visibility')} "
                 f"| {label} | {pkg.get('mathlib_pin') or '-'} "
                 f"| {pkg.get('reaudit_after') or '-'} |")
    L += ["", "## Active leases", ""]
    live = [(n, p["lease"]) for n, p in sorted(reg["packages"].items())
            if p.get("lease") and lease_live_or_none(p["lease"], now)]
    if live:
        for n, lease in live:
            L.append(f"- **{n}** — {lease.get('agent')}@{lease.get('workspace')} "
                     f"until {lease_expiry(lease).isoformat()}")
    else:
        L.append("- none")
    L += ["", "## Third-party adoptions", ""]
    if candidates is None:
        L.append("no adoption ledger found")
    else:
        cands = candidates["candidates"]
        adopted = [(n, c) for n, c in sorted(cands.items())
                   if c.get("status") == "adopted"]
        watch = sum(1 for c in cands.values()
                    if c.get("status") not in ("adopted", "rejected"))
        rejected = sum(1 for c in cands.values()
                       if c.get("status") == "rejected")
        if adopted:
            L += ["| package | upstream_pin | consumers | license |",
                  "|---|---|---|---|"]
            for n, c in adopted:
                consumers = (", ".join(str(x) for x in (c.get("consumers") or []))
                             or "-")
                L.append(f"| {n} | {c.get('upstream_pin') or '-'} "
                         f"| {consumers} | {c.get('license') or '-'} |")
            L.append("")
        L.append(f"radar: {len(adopted)} adopted (table above), "
                 f"{watch} watch-only, {rejected} rejected — full ledger: "
                 f"`lean-adoption-candidates.yaml`")
    L += ["", "## References", "",
          "- lean-pkg `DESIGN.md` (package lifecycle, receipts, promotion)",
          "- `lean-pkg llms` (agent contract)",
          ""]
    return "\n".join(L)


def cmd_wiki(args) -> int:
    reg_path = registry_path(args)
    reg = load_registry(reg_path)
    cand_path = (pathlib.Path(args.candidates).expanduser()
                 if getattr(args, "candidates", None)
                 else reg_path.parent / "lean-adoption-candidates.yaml")
    candidates = load_candidates(cand_path)
    text = render_wiki(reg, candidates)
    if args.out:
        out_p = pathlib.Path(args.out).expanduser()
        try:
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(text)
        except OSError as e:
            raise Fail(f"cannot write {out_p}: {e.strerror or e}")
        if args.json_out:
            adopted = sum(1 for c in (candidates or {}).get("candidates", {}).values()
                          if c.get("status") == "adopted")
            print_json({"ok": True, "out": str(out_p),
                        "packages": len(reg["packages"]), "adopted": adopted})
        else:
            print(f"wrote {out_p}")
    elif args.json_out:
        adopted = sum(1 for c in (candidates or {}).get("candidates", {}).values()
                      if c.get("status") == "adopted")
        print_json({"ok": True, "out": None,
                    "packages": len(reg["packages"]), "adopted": adopted,
                    "markdown": text})
    else:
        print(text, end="")
    return 0


# --------------------------------------------------------------------------
# llms — agent-oriented usage contract (paste into dispatch prompts)

LLMS_TEXT = """\
lean-pkg llms.txt — agent contract for the Lean 4 package registry

PURPOSE
A registry YAML file is the source of truth for a set of Lean 4 formalization repos.
lean-pkg is the ONLY write path; GitHub is just the visibility mirror. history[] and the
event log (lean-package-events.jsonl next to the registry) are append-only.
Registry resolution: --registry PATH, else env LEAN_PKG_REGISTRY, else ~/.lean-pkg/registry (dir with lean-packages.yaml, or the file; base env LEAN_PKG_HOME), else ./lean-packages.yaml.

STANDING RULES
- NEVER hand-edit the registry yaml. Use `lean-pkg register` / `lean-pkg update`.
- Before starting Lean work on a package: `lean-pkg board --json` (or `list --json`).
- Claim before working: one package = one workspace. Default TTL 24h; `audit --fix` sweeps expired leases. Work longer than the TTL: `renew` before it lapses (`leases --agent A --expiring 2`).
- Mutating ops are dry-run by default unless a flag says otherwise: promote --yes, audit --fix.
- Fully non-interactive: pass flags, never wait for a prompt. Use --json for machine parsing.
- Shared registry in git: --commit (env LEAN_PKG_COMMIT=1) pulls --ff-only, writes, commits the yaml + event log together; --push (LEAN_PKG_PUSH=1) also pushes (rejected as non-fast-forward: one fetch + rebase of that one commit + retry; a conflict aborts, commit kept); --no-commit overrides. Every write is covered (index add, formalization --write, claims sync, search --record, board TUI keys); dry runs and read-only commands never fetch (an explicit --commit there: stderr "ignored"). Reports on stderr; a --json payload gains "commit": {sha, pushed, error}.

STATUS ENUM AND TRANSITIONS (evidence required)
scratch -> active                  owner_project named
active -> gate-pending             lower-tier check submitted (terminal executor job identity in notes); also verified-private -> gate-pending to re-gate a new commit
gate-pending -> verified-private   `gate` accepts a receipt (schema below)
public -> public                   `gate` on an already-public package records evidence; status unchanged
verified-private -> public         `promote --yes` after a clean dry-run checklist; ONE-WAY flip
public -> upstreamed               `update --status`: a recorded mathlib-pr-merged / palomar-registered event, or a --reason with the URL
any -> archived                    `update --status archived --reason` or `audit --fix` (gh isArchived); public never returns to a private status

SUBCOMMANDS (global flags: --registry PATH --events PATH --json --commit --push --no-commit, also after the subcommand; env LEAN_PKG_REGISTRY, LEAN_PKG_HOME, LEAN_PKG_EVENTS (ignored when --registry is given: its sibling log); --version)
board     TUI on a tty, static table otherwise.       ex: lean-pkg board
list      static table.                                ex: lean-pkg list --json
status    full record + last 5 history entries.        ex: lean-pkg status my-lean
init      bootstrap an empty registry + event log (event: file name + version; --force: empty only, forced: true). ex: lean-pkg --registry ./lean-packages.yaml init
migrate   upgrade the registry schema in place to v3 (v2 adds license_spdx, formalization_yaml, palomar[], doi, reservoir, consent[];
          v3 adds claims_file, claims[], claims_synced_commit; additive, idempotent). ex: lean-pkg migrate --dry-run
record    record a D5 evidence event; validated before writing (a schema error it introduces refuses, rule named, nothing written); --field keys (all required, others refused; commit 40-hex, version integer):
          consent-recorded: who role pointer (a /home, /Users, ~ path: WARN + private_strings) | palomar-preflight-pass: commit run (same commit+run again: already recorded, exit 0)
          palomar-submitted: commit submission_id comparator_config | palomar-review: commit outcome(neutral|revision_required|rejected)
          palomar-registered: commit id version(int) comparator_config (status public|upstreamed; config tracked at HEAD) | palomar-withdrawn: id version(int) (of a recorded entry) | mathlib-pr-opened, mathlib-pr-merged: url (https://github.com/...)
          doi-minted: doi(10.NNNN/x) tag commit [concept] (rule doi-in-palomar: commit already recorded by a palomar-* event) | reservoir-listed: url (the
          package's https://reservoir.lean-lang.org/@owner/name page; Reservoir API: 404 refuses, unreachable records verified=false; a url naming neither package nor repo warns)
          retract: event ts + --reason: append-only correction (event evidence-retracted); validate and the upstreamed check ignore the entry
          ex: lean-pkg record my-lean doi-minted --field doi=10.5281/zenodo.1 --field tag=v1.0.0 --field commit=<40hex>
register  add a package; dedupes by name AND resolved local_path (~/ expanded, stored $HOME-relative; empty --path/--repo refused); auto-detects toolchain + mathlib pin; a Lean root namespace (lakefile lean_lib, else top-level *.lean) another package's checkout also uses: stderr note + `notes`, never a refusal.
  flags: --repo URL (required) --path HOME-RELATIVE (required) [--owner-project N]
         [--visibility private|public] [--status scratch|active|gate-pending|public (public: --visibility public --reason R)]
         [--upstream-target none|mathlib|palomar] [--automation TEXT] [--notes TEXT] [--workspace W] [--agent A] [--dry-run]
  --workspace/--agent are metadata only; they do NOT claim. Claiming is a separate act.
  ex: lean-pkg register my-lean --repo git@github.com:you/my-lean.git --path src/my-lean
update    set fields; manual status moves and --force need --reason (else exit 1; a refusal lists the allowed moves); empty --repo/--owner-project refused; --license-spdx takes an SPDX expression (MIT OR Apache-2.0); --paper-arxiv ID / --paper-status unpublished|preprint|published set the optional paper {arxiv, status} (an id alone: preprint). register/update/record: schema errors already in the registry (e.g. another package's) go to stderr as pre-existing, not blocking.
  ex: lean-pkg update my-lean --status gate-pending --reason "check job 4242 submitted"
  --path NEW --reason TEXT [--agent A] [--redetect] [--dry-run]: relocate (same name, history, claims; event relocated). Same path: no-op,
  exit 0. Refuses another package's path, a foreign live lease (no --force). Gate/claims not bound to NEW's HEAD are cleared (`cleared`).
claim     CAS lease; refuses if another agent holds an unexpired lease (names holder + expiry). Writes take a file lock on <registry>.lock (sibling); safe across processes on one host.
  flags: --workspace W --agent A (required) [--ttl HOURS: 24 new; a holder's re-claim keeps its TTL] [--reason TEXT] [--force (needs --reason)]
  ex: lean-pkg claim my-lean --workspace w3 --agent vera --reason "chapter 2"
release   holder-only unless --force --reason (released_by: --agent, else force). ex: lean-pkg release my-lean --agent vera
renew     holder-only restart of a LIVE lease (event claim-refreshed); a lapsed lease is refused: claim again. leases [--agent A] [--expiring H]: held leases, soonest first; doctor warns on leases lapsing within 2h or expired-unswept. Unparseable lease fields: shown INVALID, writes refused; `release NAME --force --reason TEXT` clears.
gate      validate receipt; gate-pending -> verified-private; identical re-gate = no-op "already gated".
  flags: --receipt PATH (required), --comparator PATH (optional), --refuse-private (private paths in a receipt: refuse, exit 1,
  nothing copied; default a stderr WARN naming the field + JSON private_strings[{field, kind}]), --refuse-dirty (modified tracked files; default WARN + JSON dirty[]), --refresh-formalization [--agent A] (then regenerate formalization.yaml + pointer in one step, JSON "formalization": {...}; lease + schema checked BEFORE the gate; identical re-gate with a current file: refused, nothing written)
  ex: lean-pkg gate my-lean --receipt /tmp/receipt.json --comparator /tmp/comparator.json
comparator <name> --receipt PATH   attach comparator evidence to a verified-private/public package;
  no status change; event comparator-attached; identical re-attach = no-op. It is required by promote.
  The receipt is written by comparator-receipt on a host its policy allows (a Slurm job, a host in
  COMPARATOR_RECEIPT_ALLOWED_HOSTS, or COMPARATOR_RECEIPT_ALLOW_HOST=1).
  flags: --receipt PATH (required), --check (validate only, record nothing; works at any status), --refuse-private, --refuse-dirty
  ex: lean-pkg comparator my-lean --receipt .lean-receipts/<c12>-comparator.json   |   comparator host-check: read-only, no registry: kernel, landlock ABI, bwrap, go/cargo/jq/git/lean/lake ok|warn|fail + verdict line (exit 0 can / 1 cannot)
promote   dry-run default; --yes flips GitHub visibility to public (ONE-WAY) + one notification
  (via LEAN_PKG_NOTIFY_CMD when set). Needs an authenticated `gh` (--yes: a failed gh query is a failure). Refuses without a byte-
  valid comparator receipt bound to HEAD, both receipts + comparator logs committed unmodified at HEAD, HEAD in origin/<branch> (local refs), and (paper field) a paper unpublished with no arXiv id.
  ex: lean-pkg promote my-lean          # always read the dry-run checklist first
audit     read-only without --fix (expired leases reported); --fix: sweep leases, gh -> registry visibility; toolchain/formalization.yaml drift; receipt/comparator drift judged
  at the reference branch tip (origin/HEAD, else main, else master; local refs; a checkout elsewhere: checkout-branch finding).
  --toolchain-floor V (env LEAN_PKG_TOOLCHAIN_FLOOR; default leanprover/lean4:v4.35.0-rc2): below-toolchain-floor, rc < release,
  nightly -> toolchain-unparsed, scratch/archived exempt; repo not on GitHub -> no-github-mirror (human: one line); informational: receipt-untracked (receipt file not tracked at the tip), duplicate-path (two packages, one checkout), duplicate-namespace (two checkouts, one Lean root namespace). ex: lean-pkg audit --json
validate  schema + enum + history sanity.              ex: lean-pkg validate
doctor    ok/warn/n/a: version, install, registry [source: flag|env|user-default|cwd], user-default, events [source: flag|registry-sibling|env|sibling], registry-repo (behind/ahead of origin or no origin remote, dirty registry files: warn = do not write yet; --fetch first), index, host (comparator host-check verdict; warn = cannot produce comparator receipts), tools, gh auth (account; warn = not logged in, promote needs it), python; exit 1 if the registry is unreadable or its schema version is unsupported (every other command refuses a newer version: upgrade lean-pkg).
llms      this contract (--json: {"ok": true, "text": ...}). ex: lean-pkg llms
wiki      render a Markdown status page.               ex: lean-pkg wiki --out status/index.md
index     public catalog <index>/<name>/entry.yaml + receipts/ (root: --index, env LEAN_PKG_INDEX, index/ beside the registry if present, ./index; check/render/migrate exit 1 when it is missing).
  add NAME --by HANDLE --headline 'Name :: File.lean :: One sentence.[ :: ChallengeTheorem]' [--from-registry] [--repo URL]
      [--repo-dir P] [--lower REL] [--comparator REL] [--license SPDX] [--tag T] [--field-tag T] [--consent-recorded]
      [--allow-unpushed]  (default: the pin and evidence must be on a branch of the public repo; consent must be recorded)
      validates the receipts at the pin (their commit) in a clone; trust computed; receipts copied verbatim;
      duplicate repo+commit refused; never writes local_path; event index-added (event log only).
  check [NAME ...] [--clone | --repo-dir P]   static without a source; else re-derive from bytes at the pin.
      Palomar/Reservoir verified by API when reachable, else "skipped" (never failed, never passed). exit 0/1/2.
  render [--out PATH] [--check]   Markdown table, default <index>/README.md; --check exits 1 when stale.
  migrate [--check]   rewrite index-entry/1 entries as index-entry/2 (claims derived from the headline).
claims    claims.yaml (schema claims/1) at the package repo root: named reusable results, cached in the registry.
  sync NAME   read claims.yaml at HEAD (committed bytes only); every decl must occur in its file; event claims-synced.
  list [NAME] | check [NAME]   cached claims with status and derived trust | re-validate; exit 0/1.
formalization NAME [--write] [--no-registry] [--out PATH] [--force [--reason TEXT]] [--author A ...] [--agent A]   render formalization.yaml (mathlib-initiative
  v0.4, what Palomar reads) from the registry + the repo at HEAD. Derived every run: project.name/license (license_spdx),
  automation.notes, status.sorry_count/axioms/evidence (gate receipt), status.main_results (every tracked comparator config:
  comparator_config, declaration, file; definition holes carry judged_as, null when unsigned), alignment (claims.yaml). Every other
  key is KEPT from the file; first render seeds authors from consent[] owner/author, else "[PLACEHOLDER] ..." + stderr warning (never
  invented: no paper titles). --write: idempotent; lease + update checked BEFORE writing (foreign lease: --force --reason); a differing file needs --force;
  --write also sets the registry pointer (a registry write); --write --no-registry: the file only, registry untouched (no lock/--commit). Validated against the vendored v0.4 schema (jsonschema, else structural). audit: formalization-missing/-drift. docs/formalization.md
search    TERM [--package NAME] [--offline] [--sources a,b,c] [--record [--repo-dir P]] [--json]   prior-art search.
  order: registry, index, loogle, leansearch, reservoir, palomar, github; each "ok" or "skipped: <reason>" (never zero hits).
  local score: exact alias or package name 3, keyword 2, every term word 1; registry hits: packages (name, notes, owner_project, repo; hit_kind package) and claims. exit 0 even with zero hits; 2 on usage errors.
  --record: docs/prior-art/<date>-<slug>.md in the package checkout (or --repo-dir; never the registry dir; never
  overwritten; "decision: pending" for a human) + event prior-art-searched. claim/index add print advisory notes on stderr (`notes` in --json).

RECEIPT SCHEMA (JSON object; `gate` enforces every line)
commit         40 lowercase hex; MUST equal HEAD, or HEAD must be an evidence-only descendant of it
               (only .lean-receipts/ paths differ between the two commits)
lean_toolchain string; MUST equal the lean-toolchain file committed at `commit`, stripped (the working tree never counts)
mathlib_rev    nonempty string (full mathlib rev)
sorry_count    integer; MUST be 0
axioms         list of strings; subset of gate.axioms_allowed (default exactly: propext, Quot.sound, Classical.choice)
report_sha256  64 hex chars; SHA-256 of the verification report
job_id         nonempty terminal executor job identity, unique within that executor (stored as <c12>-<job_id sanitised>.json)
checker        nonempty exact checker command/mode (e.g. serial leanchecker manifest)
generated_at   ISO-8601 timestamp; MUST parse

COMPARATOR RECEIPT SCHEMA ("comparator-receipt/2"; "comparator-receipt/1" still validates; written by comparator-receipt; `promote` re-derives every line from bytes)
schema          "comparator-receipt/2" (or "/1": legacy mechanism implied)
mechanism       "toolchain" (lake comparator + the toolchain's bundled NanoDa/con-ron; Lean v4.35.0-rc2+; what Palomar runs)
                or "legacy" (pinned comparator/lean4export/nanoda/landrun stack); comparator-receipt --mechanism auto|toolchain|legacy
commit          40 lowercase hex; MUST equal the lower-tier receipt commit, and equal HEAD or have HEAD as
                an evidence-only descendant (only .lean-receipts/ paths differ)
lean_toolchain  MUST equal the lean-toolchain file committed at `commit`, stripped
tools           legacy: {comparator, lean4export, nanoda, landrun}: 40-hex commits actually used
                toolchain: {lean_toolchain, lake_sha256, leanexport_sha256, leanchecker_sha256, nanoda_bin_sha256, con-ron_sha256}:
                64-hex sha256 of the judging binaries under `lean --print-prefix`/bin
host, job_id, generated_at   nonempty (Slurm job id, or local-<host>-<pid>-<utc-stamp>); generated_at MUST parse as ISO-8601
host_policy     optional; the runner's host-policy reason (an override run is visible here)
test_only       if present and true the receipt was produced under a test-only override and is REFUSED
result          "pass" (the runner writes a receipt only on PASS)
configs[]       MUST list exactly the comparator*.json / comparator/config*.json files tracked at HEAD
                (no omissions, no extras). Per config: path, sha256 of its HEAD bytes; challenge_module,
                solution_module, theorem_names[] (non-empty), definition_names[], permitted_axioms[]
                (subset of gate.axioms_allowed) — each MUST equal the config file at HEAD;
                a config with non-empty definition_names MUST carry judged_as {reviewer (a human, never
                a model/agent token), date YYYY-MM-DD, definitions ⊇ definition_names, basis?} in the
                file at HEAD — comparator never compares hole bodies (checked by the runner before a run
                and by every validator; judged_as is copied into the entry when present);
                log MUST be .lean-receipts/<commit12>-comparator-<cfgname>.log (cfgname = path minus
                .json, '/' -> '_'); log_sha256; result "pass". The log must contain the full line
                "Your solution is okay!" (both mechanisms; `lake comparator` prints it last, after the
                per-kernel acceptance lines). A file already at the canonical .lean-receipts path that
                differs from the receipt being recorded is refused (no silent pointer drift).

INDEX ENTRY SCHEMA ("index-entry/2"; "index-entry/1" still validates; YAML keys in this order; unknown keys refused; no local paths)
schema name repo(https) pin{commit = the receipts' commit, tag|null} lean_toolchain mathlib_rev|null license_spdx
trust(lower-tier|comparator|palomar; computed, never typed) receipts{lower|null, comparator|null}
headline[{name, file, summary, judged_as?}] (comparator trust needs every headline judged_as in a config)
claims[{id = name/slug-of-decl, decl, kind theorem, summary, judged_as, trust}] (/2 only; derived from headline at add)
palomar[{id, version}] doi|null reservoir|null formalization_yaml|null fields[] consent{recorded} added{at, by} notes

CLAIMS FILE SCHEMA ("claims/1"; claims.yaml at the package repo root; unknown keys refused; no local paths)
schema package claims[{id = package/slug [a-z0-9][a-z0-9-]*, decl, kind definition|lemma|theorem|construction|library,
summary, aliases[]?, keywords[]?, file (.lean), status reusable|internal|experimental|superseded, superseded_by|null
(required iff superseded; acyclic), citation|null, depends_on[]?}]; trust is derived, never typed

EXECUTOR EVIDENCE
The gate is executor-neutral. The frozen report named by report_sha256 must bind the exact
candidate commit/tree, source archive, toolchain/dependency pins, complete checker coverage,
terminal state/exit, and executor-specific identity/resource evidence. Slurm reports bind
terminal scheduler accounting; hosted reports bind run/attempt/job identity, terminal
conclusion, immutable workflow/control revision, runner identity, and resources. A pilot,
partial manifest, pre-terminal marker, or prior-candidate receipt is not gate evidence.

EVENT LINE SCHEMA (one JSON object per line; package events also go to history[]; index-added, prior-art-searched, registry-* are log-only)
ts      UTC ISO-8601 with offset, second precision (all written times; dates are UTC; same-second order = file order; naive = legacy UTC, doctor warns)
event   one of: registry-created, registry-migrated, registered, updated, claimed, claim-refreshed, claim-forced,
        released, release-forced, lease-expired, gate-passed, promoted, audit-fix, relocated, status-changed,
        comparator-attached, index-added, claims-synced, prior-art-searched, evidence-retracted, and the evidence events under `record`
name    package name or null
plus event-specific fields (agent, workspace, ttl_hours, reason, commit, receipt, ...)

EXIT CODES
0  success, including dry-runs and the "already gated" no-op
1  refused or validation failed (stderr has the reason; with --json stdout also gets {"ok": false, "error": ...})
2  usage error (argparse, or an interactive prompt attempted off-tty; with --json a usage refusal also prints {"ok": false, "error": ..., "usage": true})
"""


# --------------------------------------------------------------------------
# CLI

def cmd_llms(args) -> int:
    if getattr(args, "json_out", False):
        print_json({"ok": True, "text": LLMS_TEXT})
        return 0
    print(LLMS_TEXT, end="")
    return 0


def cmd_init(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_init(reg_path, events_p, args.force)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_migrate(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_migrate(reg_path, events_p, args.dry_run)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_record(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    fields: dict = {}
    for item in args.field or []:
        if "=" not in item:
            raise Fail(f"--field expects KEY=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        fields[k.strip()] = v
    out, payload = do_record(reg_path, load_registry(reg_path), events_p,
                             args.name, args.event, fields, args.reason)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_register(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_register(reg_path, load_registry(reg_path), events_p,
                               args.name, args.repo, args.path,
                               args.owner_project, args.visibility, args.status,
                               args.upstream_target, args.automation, args.notes,
                               args.workspace, args.agent, args.dry_run, reason=args.reason)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
        for note in payload.get("notes") or []:
            print(f"lean-pkg: {note}", file=sys.stderr)   # advisory (#22.7): stdout's last line is the result
    return 0


def cmd_claim(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    reg = load_registry(reg_path)
    out, payload = do_claim(reg_path, reg, events_p,
                            args.name, args.workspace, args.agent, args.ttl,
                            args.reason, args.force)
    note = prior_art_note(get_pkg(reg, args.name), events_p)
    payload["notes"] = [note] if note else []
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
        if note:
            print(note, file=sys.stderr)   # advisory: stderr, so stdout's last line is the result
    return 0


def cmd_release(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_release(reg_path, load_registry(reg_path), events_p,
                              args.name, args.agent, args.force, args.reason)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_renew(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_renew(reg_path, load_registry(reg_path), events_p,
                            args.name, args.agent, args.ttl, args.workspace, args.reason)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_leases(args) -> int:
    reg = load_registry(registry_path(args))
    now = dt.datetime.now(dt.timezone.utc)
    rows = lease_rows(reg, now, agent=args.agent, expiring_hours=args.expiring)
    if args.json_out:
        print_json({"ok": True, "now": now.isoformat(timespec="seconds"), "leases": rows})
    elif rows:
        print("\n".join(leases_lines(rows, now)))
    else:
        print("no leases" + (f" held by {args.agent}" if args.agent else "")
              + (f" expiring within {args.expiring:g}h" if args.expiring is not None else ""))
    return 0


def cmd_update(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    sets = {}
    for field in ("automation", "notes", "upstream_target", "reaudit_after",
                  "owner_project", "repo", "license_spdx", "formalization_yaml"):
        value = getattr(args, field.replace("-", "_"), None)
        if value is not None:
            sets[field] = value
    paper = {k: v for k, v in (("arxiv", args.paper_arxiv), ("status", args.paper_status)) if v is not None}
    if paper:
        if "arxiv" in paper:
            require_nonempty("--paper-arxiv", paper["arxiv"])
            paper["arxiv"] = paper["arxiv"].strip().removeprefix("arXiv:").removeprefix("arxiv:")
        sets["paper"] = paper
    if args.path is not None:
        if sets or args.status is not None:
            raise UsageError("--path is a relocation on its own; set other fields in a separate update")
        if args.force:
            raise UsageError("--force is not accepted with --path; the lease holder relocates with --agent")
        out, payload = do_relocate(reg_path, load_registry(reg_path), events_p, args.name,
                                   new_path=args.path, reason=args.reason, agent=args.agent,
                                   redetect=args.redetect, dry_run=args.dry_run)
        if args.json_out:
            print_json(payload)
        else:
            print("\n".join(out))
        return 0
    if args.dry_run:
        raise UsageError("update --dry-run is supported only with --path")
    out, payload = do_update(reg_path, load_registry(reg_path), events_p,
                             args.name, sets=sets, status=args.status,
                             reason=args.reason, redetect=args.redetect,
                             agent=args.agent, force=args.force)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def refresh_preflight(reg: dict, name: str, agent: str | None) -> None:
    """--refresh-formalization, before the gate writes anything: the package's checkout exists, no other
    agent holds a live lease (the file write needs it), and the current render passes the schema (a kept
    key that fails it would otherwise refuse only after the gate has been recorded)."""
    from . import formalization as fz
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir() or git_head(repo) is None:
        raise Fail(f"{name}: --refresh-formalization needs the git checkout at {repo}")
    refuse_invalid_lease(name, pkg.get("lease"))
    lease = pkg.get("lease")
    if lease_live(lease, dt.datetime.now(dt.timezone.utc)) and agent != lease.get("agent"):
        raise Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}; "
                   f"nothing written")
    _text, doc, _w, _existing = fz.render_for(pkg, repo)
    mode, errors = fz.validate_doc(doc)
    if errors:
        raise Fail(f"{name}: formalization.yaml would fail the {fz.SCHEMA_VERSION} schema ({mode}); fix "
                   f"its kept keys first; nothing written:\n  " + "\n  ".join(errors))


def do_gate_refresh(reg_path, reg, events_p, args) -> tuple[list[str], dict, list[str]]:
    """`gate NAME --receipt R --refresh-formalization` (#22.4): gate, then regenerate formalization.yaml
    (derived keys, including status.evidence, from the new receipt; kept keys survive) in one step.
    Refuses, writing nothing, when the gate is an identical re-gate and the file is already current."""
    from . import formalization as fz
    refresh_preflight(reg, args.name, args.agent)
    out, payload = do_gate(reg_path, reg, events_p, args.name, args.receipt, args.comparator,
                           refuse_private=args.refuse_private, refuse_dirty=args.refuse_dirty)
    gated = payload.get("event") == "gate-passed"
    pkg = get_pkg(reg, args.name)
    if not gated:
        text, _doc, _w, existing = fz.render_for(pkg, repo_path(pkg))
        if existing == text and pkg.get("formalization_yaml") == fz.target_rel(pkg):
            raise Fail(f"{args.name}: nothing to refresh: already gated with this receipt and "
                       f"{fz.target_rel(pkg)} is current; nothing written")
    f_lines, f_payload, warnings = fz.do_formalization(
        reg_path, reg, events_p, args.name, write=True, out=None, force=True, authors=None,
        agent=args.agent, reason="lean-pkg gate --refresh-formalization")
    payload["formalization"] = {k: f_payload[k] for k in ("path", "written", "unchanged", "registry_updated",
                                                         "validation", "placeholders")}
    return out + f_lines, payload, warnings


def cmd_gate(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    if getattr(args, "refresh_formalization", False):
        out, payload, warnings = do_gate_refresh(reg_path, load_registry(reg_path), events_p, args)
        for w in warnings:
            print(f"lean-pkg: warning: {w}", file=sys.stderr)
    elif getattr(args, "agent", None) is not None:
        raise UsageError("gate --agent is used only with --refresh-formalization")
    else:
        out, payload = do_gate(reg_path, load_registry(reg_path), events_p,
                               args.name, args.receipt, args.comparator,
                               refuse_private=args.refuse_private, refuse_dirty=args.refuse_dirty)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


HOST_CHECK = "host-check"


def is_host_check(args) -> bool:
    """`lean-pkg comparator host-check` (no --receipt): the read-only host check (#22.5)."""
    return getattr(args, "name", None) == HOST_CHECK and getattr(args, "receipt", None) is None


def cmd_comparator_host_check(args) -> int:
    from .comparator_receipt import host_checks
    res = host_checks()
    if args.json_out:
        print_json({"ok": True, **res})
    else:
        for c in res["checks"]:
            print(f"{c['level']} {c['item']}: {c['detail']}")
        print(f"verdict: {res['verdict']}")
    return 0 if res["can"] else 1


def cmd_comparator(args) -> int:
    if is_host_check(args):
        return cmd_comparator_host_check(args)
    if args.receipt is None:
        raise UsageError("comparator NAME needs --receipt PATH (or: lean-pkg comparator host-check)")
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_comparator(reg_path, load_registry(reg_path), events_p,
                                 args.name, args.receipt, args.check,
                                 refuse_private=args.refuse_private, refuse_dirty=args.refuse_dirty)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_promote(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, rc, payload = do_promote(reg_path, load_registry(reg_path), events_p,
                                  args.name, args.yes)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return rc


def cmd_audit(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    floor = resolve_toolchain_floor(args.toolchain_floor)
    findings = audit_registry(reg_path, load_registry(reg_path), events_p,
                              fix=args.fix, toolchain_floor=floor)
    fixed = [f for f in findings if f.get("fixed")]
    if args.json_out:
        print_json({"ok": True, "fix": args.fix, "findings": findings, "fixed": len(fixed)})
    else:
        print("\n".join(render_findings(findings)) if findings else "no findings")
    if fixed:
        print(f"audit --fix wrote {len(fixed)} change(s): commit {reg_path.name} and {events_p.name} together",
              file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# doctor

DOCTOR_TOOLS = ("lake", "lean", "elan", "git", "gh", "reuse", "bwrap", "comparator", "nanoda",
                "go", "cargo", "jq")
DOCTOR_REQUIRED_TOOLS = ("lake", "elan", "git")   # missing -> warn; the others are optional (n/a)
DOCTOR_COMPARATOR_TOOLS = ("go", "cargo", "jq")   # needed only to build the legacy comparator tools
DOCTOR_ELAN_TOOLS = ("lake", "lean")              # must be elan's, or lean-toolchain is ignored


def elan_bin() -> pathlib.Path:
    """elan's proxy directory: $ELAN_HOME/bin, else ~/.elan/bin."""
    home = os.environ.get("ELAN_HOME", "").strip()
    return (pathlib.Path(home) if home else pathlib.Path.home() / ".elan") / "bin"


def is_elan_proxy(where: str, tool: str) -> bool:
    """True when `where` (a PATH hit for `tool`) is elan's proxy: it sits in elan's bin directory, or
    resolves to the same file as the proxy there (e.g. a symlink to it)."""
    proxy = elan_bin() / tool
    p = pathlib.Path(where)
    if p.parent.resolve() == proxy.parent.resolve():
        return True
    return proxy.exists() and p.resolve() == proxy.resolve()


def package_dir() -> pathlib.Path:
    """Directory of the imported lean_pkg package (tests monkeypatch this)."""
    from . import __file__ as pkg_file
    return pathlib.Path(pkg_file).resolve().parent


def fetch_origin(root: pathlib.Path) -> str | None:
    """`git fetch --quiet --prune origin`; None on success, else the failure (first stderr line)."""
    p = git(root, "fetch", "--quiet", "--prune", "origin")
    if p.returncode == 0:
        return None
    return (p.stderr or p.stdout or "fetch failed").strip().splitlines()[0]


def ref_note(fetched: bool, fetch_error: str | None) -> str:
    if fetch_error:
        return f"local refs; fetch failed: {fetch_error}"
    return "fetched" if fetched else "local refs; --fetch to be sure"


# --------------------------------------------------------------------------
# committed mode (--commit / --push): pull --ff-only, write, commit the two registry files together

COMMIT_ENV = "LEAN_PKG_COMMIT"
PUSH_ENV = "LEAN_PKG_PUSH"
_IDENTITY_ERRORS = ("Author identity unknown", "Please tell me who you are")


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def commit_mode(args) -> str | None:
    """None, "commit" or "push" for this invocation: flags, else env; --no-commit wins; an invocation that
    writes nothing (is_write_invocation: read-only commands and dry runs) is always None."""
    if getattr(args, "no_commit", False) or not is_write_invocation(args):
        return None
    if getattr(args, "push", False) or _env_on(PUSH_ENV):
        return "push"
    if getattr(args, "commit", False) or _env_on(COMMIT_ENV):
        return "commit"
    return None


def note_ignored_commit_flags(args) -> None:
    """--commit/--push given on an invocation that writes nothing: a stderr note, not an error."""
    flags = [f for f in ("commit", "push") if getattr(args, f, False)]
    if flags and not getattr(args, "no_commit", False) and not is_write_invocation(args):
        print(f"lean-pkg: {' and '.join('--' + f for f in flags)} ignored: read-only command", file=sys.stderr)


def _first_line(p: subprocess.CompletedProcess, default: str) -> str:
    text = (p.stderr or p.stdout or "").strip()
    return text.splitlines()[0] if text else default


def _count_lines(p: pathlib.Path) -> int:
    try:
        return len(p.read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def _dirty(ctx: dict) -> list[str]:
    porcelain = git(ctx["root"], "status", "--porcelain", "--", *ctx["files"]).stdout.splitlines()
    return [line[3:] for line in porcelain if line.strip()]


def commit_preflight(args, mode: str) -> dict:
    """Before a write in committed mode: the registry sits in a git work tree on a branch, its two
    files carry no uncommitted changes, and the branch is fast-forwarded to origin/<branch> when
    origin is ahead. Returns the context commit_finish needs; raises Fail on a refusal."""
    if shutil.which("git") is None:
        raise Fail("--commit needs git on PATH")
    reg_p, ev = registry_path(args), events_path(args)
    top = git(reg_p.parent, "rev-parse", "--show-toplevel") if reg_p.parent.is_dir() else None
    if top is None or top.returncode != 0 or not top.stdout.strip():
        raise Fail(f"--commit needs the registry inside a git work tree ({reg_p.parent} is not)")
    root = pathlib.Path(top.stdout.strip())
    files = []
    for f in (reg_p, ev):
        try:
            files.append(str(f.resolve().relative_to(root.resolve())))
        except ValueError:
            raise Fail(f"--commit needs {f} inside the registry's git work tree {root}")
    br = git(root, "symbolic-ref", "-q", "--short", "HEAD")
    branch = br.stdout.strip() if br.returncode == 0 else ""
    if not branch:
        raise Fail(f"--commit needs a branch checked out in {root} (detached HEAD)")
    ctx = {"root": root, "files": files, "branch": branch, "events": ev, "push": mode == "push"}
    dirty = _dirty(ctx)
    if dirty:
        raise Fail(f"uncommitted registry changes in {', '.join(dirty)}; commit them first")
    if git(root, "remote", "get-url", "origin").returncode != 0:
        print(f"lean-pkg: no origin remote in {root}; committing locally without a pull", file=sys.stderr)
        ctx["origin"] = False
    else:
        ctx["origin"] = True
        err = fetch_origin(root)
        if err:
            print(f"lean-pkg: fetch origin failed: {err}; using local refs", file=sys.stderr)
        counts = git(root, "rev-list", "--left-right", "--count", f"refs/remotes/origin/{branch}...HEAD")
        if counts.returncode == 0 and int(counts.stdout.split()[0]) > 0:
            p = git(root, "pull", "--ff-only", "--quiet", "origin", branch)
            if p.returncode != 0:
                raise Fail(f"registry clone has diverged from origin/{branch}; reconcile by hand before "
                           f"writing ({_first_line(p, 'pull --ff-only failed')})")
    ctx["lines_before"] = _count_lines(ev)
    return ctx


def commit_message(ctx: dict) -> str:
    try:
        lines = ctx["events"].read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    new = [line for line in lines[ctx["lines_before"]:] if line.strip()]
    if not new:
        return "lean-pkg: registry update"
    try:
        last = json.loads(new[-1])
    except ValueError:
        return "lean-pkg: registry update"
    msg = f"lean-pkg: {last.get('event', 'update')}"
    if last.get("name"):
        msg += f" {last['name']}"
    if len(new) > 1:
        msg += f" (+{len(new) - 1} more)"
    return msg


FALLBACK_IDENTITY = ("lean-pkg", "lean-pkg@localhost")


_REJECTED_MARKERS = ("non-fast-forward", "[rejected]", "fetch first", "Updates were rejected")


def _push_rejected(p: subprocess.CompletedProcess) -> bool:
    """A push refused because origin moved on (not an auth or network failure)."""
    text = f"{p.stderr}\n{p.stdout}"
    return any(m in text for m in _REJECTED_MARKERS)


def _rebase_in_progress(root: pathlib.Path) -> bool:
    for d in ("rebase-merge", "rebase-apply"):
        q = git(root, "rev-parse", "--git-path", d)
        if q.returncode == 0 and (root / q.stdout.strip()).exists():
            return True
    return False


def _rebase_and_repush(ctx: dict, rejected: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    """One retry after a non-fast-forward rejection (#22.9): fetch origin, rebase our one commit onto
    origin/<branch> (the event log merges as a union: both sides only append lines), push once more.
    Retried only when our commit is the single commit ahead of origin/<branch>; a rebase conflict is
    aborted, so the local commit stays as it was. Returns the push (or the original rejection)."""
    root, branch = ctx["root"], ctx["branch"]
    upstream = f"refs/remotes/origin/{branch}"
    ahead = git(root, "rev-list", "--count", f"{upstream}..HEAD")
    if ahead.returncode != 0 or ahead.stdout.strip() != "1":
        return rejected
    if fetch_origin(root) is not None:
        return rejected
    print(f"lean-pkg: push rejected (origin/{branch} moved on); rebasing the commit and retrying once",
          file=sys.stderr)
    ev_rel = ctx["files"][-1]
    with tempfile.NamedTemporaryFile("w", suffix=".gitattributes", delete=False) as fh:
        fh.write(f"/{ev_rel} merge=union\n")
        attrs = fh.name
    try:
        rb = git(root, "-c", f"core.attributesFile={attrs}", "rebase", "--quiet", upstream)
    finally:
        os.unlink(attrs)
    if rb.returncode != 0:
        if _rebase_in_progress(root):
            git(root, "rebase", "--abort")
        print(f"lean-pkg: rebase onto origin/{branch} failed ({_first_line(rb, 'rebase failed')}); "
              f"kept the local commit", file=sys.stderr)
        return rejected
    return git(root, "push", "--quiet", "origin", branch)


def commit_finish(ctx: dict, rc: int) -> int:
    """After the subcommand in committed mode: commit the changed registry files (only those paths),
    then push when asked. Writes only to stderr; stdout stays the subcommand's. The outcome is left in
    ctx["result"] = {"sha", "pushed", "error"} (the `commit` key of a --json payload)."""
    root, branch = ctx["root"], ctx["branch"]
    res = ctx["result"] = {"sha": None, "pushed": False, "error": None}
    dirty = _dirty(ctx)
    if rc != 0:
        if dirty:
            res["error"] = f"not committed: the command failed and left {', '.join(dirty)} modified"
            print(f"lean-pkg: not committing: the command failed and left {', '.join(dirty)} modified",
                  file=sys.stderr)
        return rc
    if not dirty:
        return rc
    msg = commit_message(ctx)
    add = git(root, "add", "--", *dirty)
    if add.returncode != 0:
        res["error"] = f"commit failed: {_first_line(add, 'git add failed')}"
        print(f"lean-pkg: commit failed: {_first_line(add, 'git add failed')}; the registry files are "
              f"modified and uncommitted", file=sys.stderr)
        return 1
    argv = ("commit", "-q", "-m", msg, "--", *dirty)
    p = git(root, *argv)
    if p.returncode != 0 and any(s in (p.stderr + p.stdout) for s in _IDENTITY_ERRORS):
        name, email = FALLBACK_IDENTITY
        p = git(root, "-c", f"user.name={name}", "-c", f"user.email={email}", *argv)
        if p.returncode == 0:
            print(f"lean-pkg: committed as {name} <{email}>; set git user.name/email in {root}",
                  file=sys.stderr)
    if p.returncode != 0:
        res["error"] = f"commit failed: {_first_line(p, 'git commit failed')}"
        print(f"lean-pkg: commit failed: {_first_line(p, 'git commit failed')}; the registry files are "
              f"modified and uncommitted", file=sys.stderr)
        return 1
    res["sha"] = git(root, "rev-parse", "HEAD").stdout.strip() or None
    short = git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    print(f"lean-pkg: committed {short} on {branch}: {msg}", file=sys.stderr)
    if ctx["push"]:
        if not ctx["origin"]:
            res["error"] = "push failed: no origin remote"
            print(f"lean-pkg: push failed: no origin remote; run: git push origin {branch}", file=sys.stderr)
            return 1
        p = git(root, "push", "--quiet", "origin", branch)
        if p.returncode != 0 and _push_rejected(p):
            p = _rebase_and_repush(ctx, p)
            res["sha"] = git(root, "rev-parse", "HEAD").stdout.strip() or res["sha"]
        if p.returncode != 0:
            res["error"] = f"push failed: {_first_line(p, 'git push failed')}"
            print(f"lean-pkg: push failed: {_first_line(p, 'git push failed')}; run: git push origin {branch}",
                  file=sys.stderr)
            return 1
        res["pushed"] = True
        print(f"lean-pkg: pushed origin/{branch}", file=sys.stderr)
    return rc


def no_origin_ref_detail(root: pathlib.Path, branch: str, note: str) -> str:
    """Why behind/ahead cannot be counted: no origin remote at all, or no local origin/<branch> ref."""
    if git(root, "remote", "get-url", "origin").returncode != 0:
        return "no origin remote"
    return f"no local origin/{branch} ref to compare ({note})"


GH_AUTH_RE = re.compile(r"Logged in to github\.com (?:account|as) (\S+)")


def gh_auth_check() -> tuple[str, str]:
    """(level, detail) from `gh auth status`: ok with the github.com account, warn when gh is
    present but not logged in (promote and audit query GitHub through it), n/a without gh."""
    if shutil.which("gh") is None:
        return "n/a", "gh not on PATH"
    p = gh("auth", "status", "--hostname", "github.com")
    m = GH_AUTH_RE.search(f"{p.stdout}\n{p.stderr}")
    if m:
        return "ok", m.group(1)
    return "warn", "not logged in to github.com (promote and audit need it): run gh auth login"


def commit_now(ctx: dict) -> str:
    """The TUI's commit after each successful write (called inside its registry lock): commit_finish with
    stderr captured (curses owns the screen); returns its last line for the status bar."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        commit_finish(ctx, 0)
    ctx["lines_before"] = _count_lines(ctx["events"])
    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    return lines[-1].removeprefix("lean-pkg: ") if lines else ""


def install_check(d: pathlib.Path, fetch: bool = False) -> tuple[str, str]:
    """(level, detail) for the install: editable when the package's __init__.py is tracked by a git checkout,
    with HEAD, branch and behind/ahead against origin/<branch> (local refs unless fetch)."""
    if shutil.which("git") is None:
        return "warn", f"{d} (git not on PATH; install kind unknown)"
    top = git(d, "rev-parse", "--show-toplevel")
    tracked = git(d, "ls-files", "--error-unmatch", "__init__.py").returncode == 0
    if top.returncode != 0 or not top.stdout.strip() or not tracked:
        return "ok", f"installed {d}"   # not tracked: e.g. site-packages that sit inside a checkout
    root = pathlib.Path(top.stdout.strip())
    short = git(root, "rev-parse", "--short", "HEAD").stdout.strip() or "?"
    br = git(root, "symbolic-ref", "-q", "--short", "HEAD")
    branch = br.stdout.strip() if br.returncode == 0 else ""
    head = f"editable {root} @ {short}"
    if not branch:
        return "warn", head + " (detached HEAD)"
    fetch_error = fetch_origin(root) if fetch else None
    note = ref_note(fetch, fetch_error)
    counts = git(root, "rev-list", "--left-right", "--count", f"refs/remotes/origin/{branch}...HEAD")
    if counts.returncode != 0:
        return "warn", f"{head} on {branch}; {no_origin_ref_detail(root, branch, note)}"
    behind, ahead = (int(x) for x in counts.stdout.split())
    level = "warn" if behind else "ok"
    return level, f"{head} on {branch}; {behind} behind, {ahead} ahead of origin/{branch} ({note})"


def registry_repo_check(reg_p: pathlib.Path, ev: pathlib.Path, fetch: bool = False) -> tuple[str, str]:
    """(level, detail) for the git work tree holding the registry: HEAD, branch, behind/ahead of
    origin/<branch>, uncommitted tracked files (naming the registry files among them). warn when
    a write from here would conflict (behind), is invisible elsewhere (ahead, or the registry files
    are uncommitted) or sits on a detached HEAD; n/a outside a work tree or without git."""
    if shutil.which("git") is None:
        return "n/a", "git not on PATH"
    if not reg_p.parent.is_dir():
        return "n/a", f"{reg_p.parent} is not a directory"
    top = git(reg_p.parent, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        return "n/a", f"{reg_p.parent} is not inside a git work tree"
    root = pathlib.Path(top.stdout.strip())
    short = git(root, "rev-parse", "--short", "HEAD").stdout.strip() or "?"
    br = git(root, "symbolic-ref", "-q", "--short", "HEAD")
    branch = br.stdout.strip() if br.returncode == 0 else ""
    head = f"{root} @ {short}"
    if not branch:
        return "warn", head + " (detached HEAD; check out a branch before writing)"
    fetch_error = fetch_origin(root) if fetch else None
    note = ref_note(fetch, fetch_error)
    porcelain = git(root, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()
    dirty = [line[3:] for line in porcelain if line.strip()]
    reg_rel = []
    for f in (reg_p, ev):
        try:
            reg_rel.append(str(f.resolve().relative_to(root.resolve())))
        except ValueError:
            pass
    dirty_reg = [f for f in dirty if f in reg_rel]
    advice = []
    level = "ok"
    counts = git(root, "rev-list", "--left-right", "--count", f"refs/remotes/origin/{branch}...HEAD")
    if counts.returncode != 0:
        sync = no_origin_ref_detail(root, branch, note)
        level = "warn"
    else:
        behind, ahead = (int(x) for x in counts.stdout.split())
        sync = f"{behind} behind, {ahead} ahead of origin/{branch} ({note})"
        if behind:
            level = "warn"
            advice.append("pull before writing")
        if ahead:
            level = "warn"
            advice.append("push so other machines see it")
    if dirty_reg:
        level = "warn"
        advice.append("commit " + " and ".join(dirty_reg) + " together")
    tree = (f"{len(dirty)} uncommitted tracked file(s)" + (f" including {', '.join(dirty_reg)}" if dirty_reg else "")
            if dirty else "clean tree")
    detail = f"{head} on {branch}; {sync}; {tree}"
    if advice:
        detail += "; " + "; ".join(advice)
    return level, detail


def doctor_checks(args) -> tuple[list[dict], bool]:
    """Every doctor line as {"level", "item", "detail"}; ok is False when the registry is unreadable or its schema
    version is unsupported. Each check runs on its own: one that raises becomes a `warn <item>: internal
    error ...` line and the others still print."""
    from . import __version__
    from . import index as index_mod
    checks: list[dict] = []

    def add(level, item, detail):
        checks.append({"level": level, "item": item, "detail": detail})

    def guarded(item, fn):
        try:
            fn()
        except Exception as e:   # noqa: BLE001 -- doctor diagnoses; it never stops at one broken check
            add("warn", item, f"internal error {type(e).__name__}: {e} (LEAN_PKG_DEBUG=1 lean-pkg doctor "
                              f"for the traceback)")
            if _env_on(DEBUG_ENV):
                traceback.print_exception(type(e), e, e.__traceback__)

    add("ok", "version", f"lean-pkg {__version__}")
    fetch = bool(getattr(args, "fetch", False))
    guarded("install", lambda: _doctor_pair(add, "install", install_check(package_dir(), fetch=fetch)))
    state = {"reg_ok": True, "data": None}
    reg_p, source = registry_source(args)

    def registry_check():
        try:
            data = load_registry(reg_p, check_version=False)   # doctor reports an unsupported version itself
            ver = data.get("version")
            level = "ok" if ver in SUPPORTED_REGISTRY_VERSIONS else "warn"
            state["reg_ok"] = level == "ok"
            state["data"] = data
            add(level, "registry", f"{reg_p} (schema version {ver}, {len(data['packages'])} package(s))"
                + ("" if state["reg_ok"] else
                   f"; unsupported, supported: {', '.join(map(str, SUPPORTED_REGISTRY_VERSIONS))}")
                + f" [source: {source}]")
        except (Fail, OSError, ValueError) as e:
            state["reg_ok"] = False
            add("warn", "registry", f"{reg_p} unreadable: {e} [source: {source}]")
        checks[-1]["source"] = source

    guarded("registry", registry_check)

    def user_default_check():
        user, detail = user_default_registry()
        add("ok" if user is not None else "n/a", "user-default", detail)

    guarded("user-default", user_default_check)
    ev, ev_source = events_source(args)

    def events_check():
        add("ok" if ev.is_file() else "n/a", "events",
            f"{ev}" + ("" if ev.is_file() else " (not created yet)") + f" [source: {ev_source}]")
        checks[-1]["source"] = ev_source

    guarded("events", events_check)
    guarded("registry-repo", lambda: _doctor_pair(add, "registry-repo", registry_repo_check(reg_p, ev, fetch=fetch)))
    if state["reg_ok"] and state["data"] is not None:
        guarded("leases", lambda: _doctor_pair(add, "leases",
                                               leases_summary(state["data"], dt.datetime.now(dt.timezone.utc))))

    def time_check():
        naive = naive_timestamps(state["data"])
        add("warn" if naive else "ok", "time",
            f"{naive} naive timestamp(s) in leases/history (legacy; treated as UTC)" if naive
            else "every lease/history timestamp carries an offset")

    if state["reg_ok"] and state["data"] is not None:
        guarded("time", time_check)

    def index_check():
        root, isource = index_mod.index_source(args)
        if root.is_dir():
            add("ok", "index", f"{root} ({len(index_mod.entry_names(root))} entries) [source: {isource}]")
        else:
            add("n/a", "index", f"{root} (not present) [source: {isource}]")
        checks[-1]["source"] = isource

    guarded("index", index_check)
    def host_check():
        from .comparator_receipt import host_checks
        res = host_checks()
        failing = [c["item"] for c in res["checks"] if c["level"] == "fail"]
        add("ok" if res["can"] else "warn", "host", res["verdict"] if res["can"] else
            f"this host cannot produce comparator receipts (failing: {', '.join(failing)}); "
            f"details: lean-pkg comparator host-check")

    guarded("host", host_check)
    for t in DOCTOR_TOOLS:
        guarded(f"tool {t}", lambda t=t: _doctor_tool(add, t))
        if t == "gh":
            guarded("gh auth", lambda: _doctor_pair(add, "gh auth", gh_auth_check()))
    add("ok", "python", f"{sys.version.split()[0]} ({sys.executable})")
    return checks, state["reg_ok"]


def _doctor_pair(add, item: str, pair: tuple[str, str]) -> None:
    add(pair[0], item, pair[1])


def _doctor_tool(add, t: str) -> None:
    where = shutil.which(t)
    if where and t in DOCTOR_ELAN_TOOLS and not is_elan_proxy(where, t):
        add("warn", f"tool {t}", f"{where} is not elan's ({elan_bin() / t}); a {t} outside elan ignores "
            f"each project's lean-toolchain: put {elan_bin()} first on PATH")
    elif where:
        add("ok", f"tool {t}", where)
    elif t in DOCTOR_COMPARATOR_TOOLS:
        add("n/a", f"tool {t}", "not on PATH (needed by comparator-receipt to build the legacy comparator tools)")
    else:
        add("warn" if t in DOCTOR_REQUIRED_TOOLS else "n/a", f"tool {t}", "not on PATH")


def cmd_doctor(args) -> int:
    checks, ok = doctor_checks(args)
    if args.json_out:
        print_json({"ok": ok, "checks": checks})
    else:
        for c in checks:
            print(f"{c['level']} {c['item']}: {c['detail']}")
    return 0 if ok else 1


def cmd_validate(args) -> int:
    reg_path = registry_path(args)
    try:
        data = yaml.safe_load(reg_path.read_text())
    except FileNotFoundError:
        raise Fail(registry_not_found(reg_path))
    except yaml.YAMLError as e:
        raise Fail(f"registry is not valid YAML: {e}")
    errors = validate_registry(data)
    if args.json_out:
        print_json({"ok": not errors, "valid": not errors, "errors": errors,
                    "registry": str(reg_path)})
    else:
        for e in errors:
            print(f"  FAIL {e}")
        print("\nVALID" if not errors else f"\nINVALID ({len(errors)} error(s))")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="lean-pkg",
        description="Lean 4 package registry: board, leases, trust gate, "
                    "promotion")
    ap.add_argument("--registry", default=None,
                    help=f"registry yaml (default: ${REGISTRY_ENV}, else "
                         f"~/.lean-pkg/registry, else ./{DEFAULT_REGISTRY_NAME})")
    ap.add_argument("--events", default=None,
                    help=f"event log jsonl (default: {EVENTS_NAME} next to the "
                         f"registry)")
    ap.add_argument("--json", dest="json_out", action="store_true",
                    help="machine-readable JSON on stdout (exit codes unchanged)")
    ap.add_argument("--commit", action="store_true",
                    help=f"write commands: pull --ff-only first, then commit the registry and event log "
                         f"together (default: ${COMMIT_ENV}=1)")
    ap.add_argument("--push", action="store_true",
                    help=f"--commit, then push to origin (default: ${PUSH_ENV}=1)")
    ap.add_argument("--no-commit", action="store_true",
                    help=f"never commit or push, overriding ${COMMIT_ENV} and ${PUSH_ENV}")
    from . import __version__
    ap.add_argument("--version", action="version", version=f"lean-pkg {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    def add_json(s):
        # Repeat --json on every subparser so BOTH `lean-pkg --json list` and
        # `lean-pkg list --json` work. SUPPRESS keeps the global value when
        # the flag is absent after the subcommand.
        s.add_argument("--json", dest="json_out", action="store_true",
                       default=argparse.SUPPRESS,
                       help="machine-readable JSON on stdout")
        s.add_argument("--commit", action="store_true", default=argparse.SUPPRESS,
                       help="write commands: pull --ff-only, then commit the registry files")
        s.add_argument("--push", action="store_true", default=argparse.SUPPRESS,
                       help="--commit, then push to origin")
        s.add_argument("--no-commit", dest="no_commit", action="store_true", default=argparse.SUPPRESS,
                       help="never commit or push (overrides the env)")

    s = sub.add_parser("board", help="curses TUI on a tty; static table otherwise")
    add_json(s)
    s.set_defaults(fn=cmd_board)
    s = sub.add_parser("list", help="static table, pipe-friendly")
    add_json(s)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("status", help="full record + last 5 history entries")
    s.add_argument("name")
    add_json(s)
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("init", help="bootstrap an empty registry and its event log")
    s.add_argument("--force", action="store_true",
                   help="re-create only if the existing registry is empty")
    add_json(s)
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("migrate", help="upgrade the registry schema in place (additive; idempotent)")
    s.add_argument("--dry-run", action="store_true", help="print the changes; write nothing")
    add_json(s)
    s.set_defaults(fn=cmd_migrate)

    s = sub.add_parser("record", help="record a publication evidence event (consent, Palomar, DOI, Reservoir, Mathlib PR)")
    s.add_argument("name")
    s.add_argument("event", help="one of: " + ", ".join(EVIDENCE_EVENTS) + f", or {RETRACT_EVENT} "
                   "(--field event=E --field ts=T --reason TEXT: mark an evidence entry retracted)")
    s.add_argument("--field", action="append", metavar="KEY=VALUE",
                   help="event field (repeatable); required keys per event are listed by `lean-pkg llms`")
    s.add_argument("--reason", default="", help="free-text reason stored with the event")
    add_json(s)
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("register", help="the ONLY write path for new packages: "
                                        "validated, deduping, auto-detecting")
    s.add_argument("name")
    s.add_argument("--repo", required=True, help="git URL (or local bare path)")
    s.add_argument("--path", required=True,
                   help="local checkout path, $HOME-relative")
    s.add_argument("--owner-project", default=None)
    s.add_argument("--visibility", choices=VISIBILITIES, default="private")
    s.add_argument("--status", choices=REGISTER_STATUSES, default="active",
                   help="public only with --visibility public and --reason (an already-public repo)")
    s.add_argument("--upstream-target", choices=UPSTREAM_TARGETS, default="none")
    s.add_argument("--automation", default="")
    s.add_argument("--notes", default="")
    s.add_argument("--reason", default="", help="required with --status public")
    s.add_argument("--workspace", default=None,
                   help="metadata only; does NOT claim")
    s.add_argument("--agent", default=None, help="metadata only; does NOT claim")
    s.add_argument("--ttl", type=int, default=None,
                   help="accepted and ignored (register never claims; claim separately with "
                        "`lean-pkg claim NAME --ttl H`)")
    s.add_argument("--dry-run", action="store_true",
                   help="print the would-be entry as YAML without writing")
    add_json(s)
    s.set_defaults(fn=cmd_register)

    s = sub.add_parser("claim", help="CAS lease on a package (24h default TTL)")
    s.add_argument("name")
    s.add_argument("--workspace", required=True)
    s.add_argument("--agent", required=True)
    s.add_argument("--ttl", type=int, default=None, metavar="HOURS",
                   help=f"lease TTL (default: {DEFAULT_TTL_HOURS} for a new claim; a holder's re-claim keeps "
                        f"the lease's current ttl_hours)")
    s.add_argument("--reason", default="")
    s.add_argument("--force", action="store_true",
                   help="override a live lease; requires --reason")
    add_json(s)
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("release", help="release a lease (holder-only unless --force)")
    s.add_argument("name")
    s.add_argument("--agent", default=None,
                   help="the holder; with --force, the acting agent recorded as released_by")
    s.add_argument("--force", action="store_true", help="release another agent's lease; requires --reason")
    s.add_argument("--reason", default="", help="free-text reason stored with the event")
    add_json(s)
    s.set_defaults(fn=cmd_release)

    s = sub.add_parser("renew", help="restart your own live lease from now (holder-only; refuses once it "
                                     "has lapsed: claim again)")
    s.add_argument("name")
    s.add_argument("--agent", required=True)
    s.add_argument("--ttl", type=int, default=None, metavar="HOURS",
                   help="new TTL (default: the lease's current ttl_hours)")
    s.add_argument("--workspace", default=None, help="move the lease to this workspace")
    s.add_argument("--reason", default="")
    add_json(s)
    s.set_defaults(fn=cmd_renew)

    s = sub.add_parser("leases", help="held leases, soonest expiry first; read-only")
    s.add_argument("--agent", default=None, help="only leases held by this agent")
    s.add_argument("--expiring", type=nonneg_hours, default=None, metavar="HOURS",
                   help="only leases that lapse within HOURS (or have already lapsed)")
    add_json(s)
    s.set_defaults(fn=cmd_leases)

    s = sub.add_parser("update", help="set fields of an already-registered "
                                      "package (register is for NEW ones)")
    s.add_argument("name")
    s.add_argument("--automation", help="AI-disclosure note; required by mathlib "
                                        "when upstream_target is mathlib")
    s.add_argument("--notes")
    s.add_argument("--upstream-target", dest="upstream_target",
                   choices=UPSTREAM_TARGETS)
    s.add_argument("--reaudit-after", dest="reaudit_after", metavar="ISO8601")
    s.add_argument("--owner-project", dest="owner_project")
    s.add_argument("--repo", help="remote URL (e.g. after publishing a private dep)")
    s.add_argument("--license-spdx", dest="license_spdx", metavar="SPDX",
                   help="the package's license as an SPDX id or expression, e.g. 'MIT OR Apache-2.0' (what the index and Reservoir expect)")
    s.add_argument("--formalization-yaml", dest="formalization_yaml", metavar="REL",
                   help="repository-relative path of the package's formalization.yaml")
    s.add_argument("--paper-arxiv", dest="paper_arxiv", metavar="ID",
                   help="arXiv id of the paper this package verifies (sets paper.arxiv; status defaults to "
                        "preprint)")
    s.add_argument("--paper-status", dest="paper_status", choices=PAPER_STATUSES,
                   help="the verified paper's status; promote refuses while unpublished without an arXiv id")
    s.add_argument("--status", choices=STATUSES,
                   help="manual transitions only; verified-private is gate's "
                        "and public is promote's")
    s.add_argument("--reason", default="", help="required for --status and --force")
    s.add_argument("--redetect", action="store_true",
                   help="re-read lean_toolchain and mathlib_pin from the repo")
    s.add_argument("--agent", default=None, help="lease holder, if one is live")
    s.add_argument("--force", action="store_true",
                   help="override a live lease held by another agent; needs --reason")
    s.add_argument("--path", default=None, metavar="HOME-RELATIVE",
                   help="relocate: move local_path to another checkout, keeping name, history "
                        "and claims (needs --reason; event relocated; commit-bound evidence "
                        "not bound to the new HEAD is cleared)")
    s.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="with --path: print the would-be changes, write nothing")
    add_json(s)
    s.set_defaults(fn=cmd_update)

    s = sub.add_parser("gate", help="validate a lower-tier receipt; "
                                    "gate-pending -> verified-private")
    s.add_argument("name")
    s.add_argument("--receipt", required=True, help="path to receipt JSON")
    s.add_argument("--comparator", default=None, metavar="PATH",
                   help="optional comparator receipt JSON; "
                        "required later by promote")
    s.add_argument("--refuse-private", action="store_true",
                   help="refuse (exit 1, nothing copied) when a receipt holds private strings; "
                        "default is a WARN line")
    s.add_argument("--refuse-dirty", action="store_true",
                   help="refuse when tracked files are modified in the checkout; default is a WARN "
                        "line (receipts are always checked against the bound commit)")
    s.add_argument("--refresh-formalization", action="store_true",
                   help="after the gate, regenerate the package's formalization.yaml (derived keys, "
                        "including status.evidence; kept keys survive) and set its registry pointer; "
                        "refuses when nothing would change")
    s.add_argument("--agent", default=None,
                   help="with --refresh-formalization: the live lease holder, if any (the file write needs it)")
    add_json(s)
    s.set_defaults(fn=cmd_gate)

    s = sub.add_parser("comparator", help="attach a comparator receipt to a gated "
                                          "package; no status change")
    s.add_argument("name")
    s.add_argument("--receipt", default=None, help="path to comparator receipt JSON (required, except for "
                                                   "`comparator host-check`)")
    s.add_argument("--check", action="store_true",
                   help="validate only; record nothing (works at any status)")
    s.add_argument("--refuse-private", action="store_true",
                   help="refuse (exit 1, nothing copied) when the receipt holds private strings; "
                        "default is a WARN line")
    s.add_argument("--refuse-dirty", action="store_true",
                   help="refuse when tracked files are modified in the checkout; default is a WARN line")
    add_json(s)
    s.set_defaults(fn=cmd_comparator)

    s = sub.add_parser("promote", help="promotion checklist; DRY-RUN unless --yes")
    s.add_argument("name")
    s.add_argument("--yes", action="store_true",
                   help="execute: gh repo edit --visibility public (ONE-WAY)")
    add_json(s)
    s.set_defaults(fn=cmd_promote)

    s = sub.add_parser("audit", help="report expired leases, gh visibility, drift; read-only without --fix")
    s.add_argument("--fix", action="store_true",
                   help="clear expired leases and update visibility/archive state from gh")
    s.add_argument("--toolchain-floor", default=None, metavar="VERSION",
                   help=f"oldest acceptable lean_toolchain (default: ${TOOLCHAIN_FLOOR_ENV}, "
                        f"else {DEFAULT_TOOLCHAIN_FLOOR})")
    add_json(s)
    s.set_defaults(fn=cmd_audit)

    s = sub.add_parser("validate", help="schema + enum + history sanity; exit 0/1")
    add_json(s)
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("doctor", help="installation, registry, index and tool diagnostics; "
                                      "exit 1 when the registry is unreadable or its schema "
                                      "version is unsupported")
    s.add_argument("--fetch", action="store_true",
                   help="git fetch origin in the tool checkout and the registry repo before comparing "
                        "(default: local refs)")
    add_json(s)
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("llms", help="print the agent-oriented usage contract")
    add_json(s)
    s.set_defaults(fn=cmd_llms)

    s = sub.add_parser("wiki", help="render a Markdown status page")
    s.add_argument("--out", default=None,
                   help="write the Markdown here instead of stdout")
    s.add_argument("--candidates", default=None,
                   help="adoption ledger path (default: lean-adoption-candidates.yaml "
                        "next to the registry)")
    add_json(s)
    s.set_defaults(fn=cmd_wiki)

    from . import index as index_mod
    index_mod.add_parsers(sub, add_json)
    from . import claims as claims_mod
    claims_mod.add_parsers(sub, add_json)
    from . import search as search_mod
    search_mod.add_parsers(sub, add_json)
    from . import formalization as formalization_mod
    formalization_mod.add_parsers(sub, add_json)

    ap.set_defaults(fn=cmd_board)
    args = ap.parse_args(argv)
    try:
        with write_lock(args):
            return run_command(args)
    except Fail as e:   # the lock itself could not be taken
        _report_fail(args, e)
        return 1
    except OSError as e:   # init could not create the registry directory for its lock
        _report_fail(args, Fail(f"cannot prepare the registry lock: {e}"))
        return 1


def _report_fail(args, e: Fail) -> None:
    if getattr(args, "json_out", False):
        print_json({"ok": False, "error": str(e)})
    print(f"lean-pkg: {e}", file=sys.stderr)


def board_interactive() -> bool:
    """`board` opens the curses TUI (which can write) only on a tty; otherwise it prints the table."""
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"


# Writes that append an event (or run the TUI) without holding the registry lock for the whole command:
# they lock just around each write (they may use the network, or wait on the keyboard).
UNLOCKED_WRITES = ("index", "search", "board", None)


def is_write_invocation(args) -> bool:
    """Does this invocation, as parsed, write the registry or its event log? The one predicate behind the
    registry lock (locks_registry) and committed mode (commit_mode). Dry runs and read-only forms are not
    writes: register/update/migrate --dry-run, promote without --yes, audit without --fix, comparator
    --check, formalization without --write or with --no-registry, claims list/check, index check/render/migrate, search without
    --record, board off a tty or with --json."""
    cmd = getattr(args, "cmd", None)
    if cmd in ("init", "record", "register", "claim", "release", "renew", "update", "gate", "migrate"):
        return not getattr(args, "dry_run", False)
    if cmd == "comparator":
        return not getattr(args, "check", False) and not is_host_check(args)
    if cmd == "promote":
        return bool(getattr(args, "yes", False))
    if cmd == "audit":
        return bool(getattr(args, "fix", False))
    if cmd == "formalization":   # --write --no-registry writes only the package's file (#22.8)
        return bool(getattr(args, "write", False)) and not getattr(args, "no_registry", False)
    if cmd == "claims":
        return getattr(getattr(args, "claims_fn", None), "__name__", "") == "run_sync"
    if cmd == "index":
        return getattr(getattr(args, "index_fn", None), "__name__", "") == "run_add"
    if cmd == "search":
        return bool(getattr(args, "record", False))
    if cmd in ("board", None):
        return (getattr(args, "fn", None) is cmd_board and not getattr(args, "json_out", False)
                and board_interactive())
    return False


def locks_registry(args) -> bool:
    """Does this invocation hold the registry lock for the whole command (load -> check -> mutate -> save
    -> append), commit preflight and commit included? Every write except UNLOCKED_WRITES (`index add` and
    `search --record` lock just around their append; the TUI around each key's write)."""
    return is_write_invocation(args) and getattr(args, "cmd", None) not in UNLOCKED_WRITES


def write_lock(args):
    """registry_lock(registry) when this invocation writes and the registry exists (init: its directory
    is created first); otherwise a no-op context."""
    if not locks_registry(args):
        return contextlib.nullcontext()
    reg_p = registry_path(args)
    if getattr(args, "cmd", None) == "init":
        reg_p.parent.mkdir(parents=True, exist_ok=True)
    elif not reg_p.is_file():
        return contextlib.nullcontext()   # the command reports "registry not found" itself
    return registry_lock(reg_p)


DEBUG_ENV = "LEAN_PKG_DEBUG"


def _registry_lock_if_present(args):
    reg_p = registry_path(args)
    return registry_lock(reg_p) if reg_p.is_file() else contextlib.nullcontext()


def _oserror_fail(e: OSError) -> Fail:
    where = f" {e.filename}" if getattr(e, "filename", None) else ""
    return Fail(f"cannot access{where}: {e.strerror or e}")


def _emit_with_commit(text: str, ctx: dict | None, rc_before: int, rc: int) -> None:
    """Re-emit a --json command's captured stdout; in committed mode a single JSON object gains
    "commit": {"sha", "pushed", "error"} (and ok: false when the commit or push failed)."""
    if ctx is not None and "result" in ctx:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            payload["commit"] = dict(ctx["result"])
            if rc_before == 0 and rc != 0:
                payload["ok"] = False
            print_json(payload)
            return
    sys.stdout.write(text)


def run_command(args) -> int:
    """Run the subcommand; refusals map to exit 2/1; an OSError is a clean refusal (exit 1); an unexpected
    exception still lets commit_finish report (and not commit), then exits 1 with a one-line internal
    error (traceback: LEAN_PKG_DEBUG=1). Committed mode: the preflight (fetch, pull --ff-only) runs only
    for an invocation that writes, after argparse has validated it; with --json the command's stdout is
    captured so the payload can carry the commit outcome."""
    note_ignored_commit_flags(args)
    mode = commit_mode(args)
    ctx = None
    crash = None
    buf = io.StringIO() if (mode and getattr(args, "json_out", False)) else None
    with contextlib.redirect_stdout(buf) if buf is not None else contextlib.nullcontext():
        try:
            if mode:
                with _registry_lock_if_present(args):
                    ctx = commit_preflight(args, mode)
                args.commit_ctx = ctx   # the TUI commits after each of its writes
            rc = args.fn(args)
        except UsageError as e:
            if getattr(args, "json_out", False):
                print_json({"ok": False, "error": str(e), "usage": True})
            print(f"lean-pkg: {e}", file=sys.stderr)
            rc = 2
        except Fail as e:
            _report_fail(args, e)
            rc = 1
        except OSError as e:   # an unwritable path, a missing file: a refusal, not an internal error
            _report_fail(args, _oserror_fail(e))
            rc = 1
        except Exception as e:   # noqa: BLE001 -- the top-level handler: report, never a bare traceback
            crash = e
            rc = 1
    rc_before = rc
    if ctx is not None:
        with _registry_lock_if_present(args):
            rc = commit_finish(ctx, rc)
    if buf is not None:
        _emit_with_commit(buf.getvalue(), ctx, rc_before, rc)
    if crash is not None:
        if _env_on(DEBUG_ENV):
            traceback.print_exception(type(crash), crash, crash.__traceback__)
        msg = f"internal error: {type(crash).__name__}: {crash}"
        if getattr(args, "json_out", False):
            print_json({"ok": False, "error": msg})
        print(f"lean-pkg: {msg}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
